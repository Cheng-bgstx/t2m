/**
 * Enhanced TrackingHelper with improved motion transition support
 * 
 * This module extends the original TrackingHelper with additional features
 * for smoother transitions, especially important for AI-generated motions
 * that may have different characteristics than pre-recorded clips.
 */

import * as THREE from 'three';
import {
  quatMultiply,
  quatInverse,
  yawComponent,
  linspaceRows,
  slerpMany
} from './utils/math.js';

function clampIndex(idx, length) {
  if (idx < 0) {
    return 0;
  }
  if (idx >= length) {
    return length - 1;
  }
  return idx;
}

function toFloat32Rows(rows) {
  if (!Array.isArray(rows)) {
    return null;
  }
  return rows.map((row) => Float32Array.from(row));
}

function normalizeMotionClip(clip) {
  if (!clip || typeof clip !== 'object') {
    return null;
  }
  const jointPosRaw = toFloat32Rows(clip.joint_pos ?? clip.jointPos);
  const rootPos = toFloat32Rows(clip.root_pos ?? clip.rootPos);
  const rootQuat = toFloat32Rows(clip.root_quat ?? clip.rootQuat);
  if (!jointPosRaw || !rootPos || !rootQuat) {
    return null;
  }
  return { jointPos: jointPosRaw, rootPos, rootQuat };
}

/**
 * Enhanced TrackingHelper with configurable transition options
 */
export class TrackingHelper {
  constructor(config) {
    this.transitionSteps = config.transition_steps ?? 100;
    this.datasetJointNames = config.dataset_joint_names ?? [];
    this.policyJointNames = config.policy_joint_names ?? [];
    this.motions = {};
    this.nJoints = this.datasetJointNames.length || this.policyJointNames.length;
    this.transitionLen = 0;
    this.motionLen = 0;

    // Enhanced transition options
    this.transitionConfig = {
      // Base transition duration in simulation steps
      baseTransitionSteps: config.transition_steps ?? 100,
      
      // Minimum transition steps (prevents too-fast transitions)
      minTransitionSteps: config.min_transition_steps ?? 30,
      
      // Maximum transition steps (prevents too-slow transitions)
      maxTransitionSteps: config.max_transition_steps ?? 200,
      
      // Adaptive transition: adjust based on pose difference
      adaptiveTransition: config.adaptive_transition ?? true,
      
      // Pose difference threshold for triggering longer transition
      poseDifferenceThreshold: config.pose_difference_threshold ?? 0.5,
      
      // Multiplier for high-difference transitions
      highDifferenceMultiplier: config.high_difference_multiplier ?? 1.5,
      
      // Smoothing for generated motions (helps with AI-generated jitter)
      enableSmoothing: config.enable_smoothing ?? false,
      smoothWindow: config.smooth_window ?? 3,
      
      // Velocity-aware transitions
      velocityAware: config.velocity_aware ?? true,
      velocityScaleFactor: config.velocity_scale_factor ?? 0.5
    };

    this.mapPolicyToDataset = this._buildPolicyToDatasetMap();

    for (const [name, clip] of Object.entries(config.motions ?? {})) {
      const normalized = normalizeMotionClip(clip);
      if (!normalized) {
        console.warn('TrackingHelper: invalid motion clip', name);
        continue;
      }
      this.motions[name] = normalized;
    }

    if (!this.motions.default) {
      throw new Error('TrackingHelper requires a "default" motion');
    }

    this.refJointPos = [];
    this.refRootQuat = [];
    this.refRootPos = [];
    this.refIdx = 0;
    this.refLen = 0;
    this.currentName = 'default';
    this.currentDone = true;
    
    // Track velocity for velocity-aware transitions
    this.lastJointVel = null;
    this.lastRootVel = null;
  }

  availableMotions() {
    return Object.keys(this.motions);
  }

  addMotions(motions, options = {}) {
    const added = [];
    const skipped = [];
    const invalid = [];
    const allowOverwrite = !!options.overwrite;

    if (!motions || typeof motions !== 'object') {
      return { added, skipped, invalid };
    }

    for (const [name, clip] of Object.entries(motions)) {
      if (!name) {
        invalid.push(name);
        continue;
      }
      if (!allowOverwrite && this.motions[name]) {
        skipped.push(name);
        continue;
      }
      let normalized = normalizeMotionClip(clip);
      
      // Apply smoothing if enabled and this is a new/generated motion
      if (normalized && this.transitionConfig.enableSmoothing && options.smooth) {
        normalized = this._applySmoothing(normalized);
      }
      
      if (!normalized) {
        invalid.push(name);
        continue;
      }
      this.motions[name] = normalized;
      added.push(name);
    }

    return { added, skipped, invalid };
  }

  /**
   * Apply smoothing to motion data to reduce jitter
   */
  _applySmoothing(motion) {
    const window = this.transitionConfig.smoothWindow;
    if (window < 2) return motion;

    const halfWindow = Math.floor(window / 2);
    
    const smoothArray = (arr) => {
      const result = [];
      for (let i = 0; i < arr.length; i++) {
        let sum = new Float32Array(arr[0].length);
        let count = 0;
        for (let j = -halfWindow; j <= halfWindow; j++) {
          const idx = i + j;
          if (idx >= 0 && idx < arr.length) {
            for (let k = 0; k < sum.length; k++) {
              sum[k] += arr[idx][k];
            }
            count++;
          }
        }
        result.push(Float32Array.from(sum.map(v => v / count)));
      }
      return result;
    };

    return {
      jointPos: smoothArray(motion.jointPos),
      rootPos: smoothArray(motion.rootPos),
      rootQuat: motion.rootQuat // Don't smooth quaternions
    };
  }

  reset(state) {
    this.currentDone = true;
    this.refIdx = 0;
    this.refLen = 0;
    this.transitionLen = 0;
    this.motionLen = 0;
    this.refJointPos = [];
    this.refRootQuat = [];
    this.refRootPos = [];
    this.currentName = 'default';
    this.lastJointVel = null;
    this.lastRootVel = null;
    this.requestMotion('default', state);
  }

  requestMotion(name, state) {
    if (!this.motions[name]) {
      return false;
    }
    if ((this.currentName === 'default' && this.currentDone) || name === 'default') {
      this._startMotionFromCurrent(name, state);
      return true;
    }
    return false;
  }

  isReady() {
    return this.refLen > 0;
  }

  playbackState() {
    const clampedIdx = Math.max(0, Math.min(this.refIdx, Math.max(this.refLen - 1, 0)));
    const transitionLen = this.transitionLen ?? 0;
    const motionLen = this.motionLen ?? 0;
    const inTransition = transitionLen > 0 && clampedIdx < transitionLen;
    return {
      available: this.refLen > 0,
      currentName: this.currentName,
      currentDone: this.currentDone,
      refIdx: clampedIdx,
      refLen: this.refLen,
      transitionLen,
      motionLen,
      inTransition,
      isDefault: this.currentName === 'default'
    };
  }

  advance() {
    if (this.refLen === 0) {
      return;
    }
    if (this.refIdx < this.refLen - 1) {
      this.refIdx += 1;
      if (this.refIdx === this.refLen - 1) {
        this.currentDone = true;
      }
    }
  }

  getFrame(index) {
    const clamped = clampIndex(index, this.refLen);
    return {
      jointPos: this.refJointPos[clamped],
      rootQuat: this.refRootQuat[clamped],
      rootPos: this.refRootPos[clamped]
    };
  }

  _readCurrentState(state) {
    if (state) {
      return {
        jointPos: Array.from(state.jointPos),
        rootPos: Array.from(state.rootPos),
        rootQuat: Array.from(state.rootQuat)
      };
    }

    const defaultMotion = this.motions['default'];
    const fallbackPos = defaultMotion?.rootPos?.[0] ?? new Float32Array([0.0, 0.0, 0.78]);
    const fallbackQuat = defaultMotion?.rootQuat?.[0] ?? [1.0, 0.0, 0.0, 0.0];
    const fallbackJoint = defaultMotion?.jointPos?.[0] ?? new Float32Array(this.nJoints);

    return {
      jointPos: Array.from(fallbackJoint),
      rootPos: Array.from(fallbackPos),
      rootQuat: Array.from(fallbackQuat)
    };
  }

  /**
   * Calculate pose difference between two states
   */
  _calculatePoseDifference(curr, target) {
    let jointDiff = 0;
    for (let i = 0; i < curr.jointPos.length; i++) {
      jointDiff += Math.abs(curr.jointPos[i] - target.jointPos[i]);
    }
    jointDiff /= curr.jointPos.length;

    const rootPosDiff = Math.sqrt(
      Math.pow(curr.rootPos[0] - target.rootPos[0], 2) +
      Math.pow(curr.rootPos[1] - target.rootPos[1], 2) +
      Math.pow(curr.rootPos[2] - target.rootPos[2], 2)
    );

    return jointDiff + rootPosDiff;
  }

  /**
   * Calculate adaptive transition steps based on pose difference and velocity
   */
  _calculateAdaptiveTransitionSteps(curr, firstFrame, currentVel) {
    const config = this.transitionConfig;
    
    if (!config.adaptiveTransition) {
      return config.baseTransitionSteps;
    }

    // Calculate pose difference
    const poseDiff = this._calculatePoseDifference(curr, firstFrame);
    
    // Base steps
    let steps = config.baseTransitionSteps;
    
    // Adjust based on pose difference
    if (poseDiff > config.poseDifferenceThreshold) {
      steps = Math.floor(steps * config.highDifferenceMultiplier);
    }
    
    // Adjust based on velocity (higher velocity = longer transition)
    if (config.velocityAware && currentVel) {
      const velMagnitude = Math.sqrt(
        currentVel.rootVel[0] ** 2 + 
        currentVel.rootVel[1] ** 2 + 
        currentVel.rootVel[2] ** 2
      );
      steps = Math.floor(steps * (1 + velMagnitude * config.velocityScaleFactor));
    }
    
    // Clamp to min/max
    return Math.max(config.minTransitionSteps, 
           Math.min(config.maxTransitionSteps, steps));
  }

  _alignMotionToCurrent(motion, curr) {
    const p0 = new THREE.Vector3(...motion.rootPos[0]);
    const pc = new THREE.Vector3(...curr.rootPos);

    const q0 = yawComponent(motion.rootQuat[0]);
    const qc = yawComponent(curr.rootQuat);
    const qDeltaWxyz = quatMultiply(qc, quatInverse(q0));
    const qDelta = new THREE.Quaternion(qDeltaWxyz[1], qDeltaWxyz[2], qDeltaWxyz[3], qDeltaWxyz[0]);

    const jointPos = motion.jointPos.map((row) => Float32Array.from(row));

    const offset = new THREE.Vector3(pc.x, pc.y, p0.z);
    const rootPos = motion.rootPos.map((row) => {
      const pos = new THREE.Vector3(...row);
      pos.sub(p0).applyQuaternion(qDelta).add(offset);
      return Float32Array.from([pos.x, pos.y, pos.z]);
    });

    const rootQuat = motion.rootQuat.map((row) => {
      const q = new THREE.Quaternion(row[1], row[2], row[3], row[0]);
      const aligned = qDelta.clone().multiply(q);
      return Float32Array.from([aligned.w, aligned.x, aligned.y, aligned.z]);
    });

    return { jointPos, rootQuat, rootPos };
  }

  _buildTransition(curr, firstFrame, steps) {
    if (steps <= 0) {
      return {
        jointPos: [],
        rootQuat: [],
        rootPos: []
      };
    }

    const jointPos = linspaceRows(curr.jointPos, firstFrame.jointPos[0], steps);
    const rootPos = linspaceRows(curr.rootPos, firstFrame.rootPos[0], steps);
    const rootQuat = slerpMany(curr.rootQuat, firstFrame.rootQuat[0], steps);

    return { jointPos, rootPos, rootQuat };
  }

  _startMotionFromCurrent(name, state) {
    const curr = this._readCurrentState(state);
    if (state && this.mapPolicyToDataset) {
      curr.jointPos = this._mapPolicyJointPosToDataset(curr.jointPos);
    }
    
    const motion = this.motions[name];
    const aligned = this._alignMotionToCurrent(motion, curr);
    const firstFrame = {
      jointPos: aligned.jointPos,
      rootQuat: aligned.rootQuat,
      rootPos: aligned.rootPos
    };

    // Calculate adaptive transition steps
    const transitionSteps = this._calculateAdaptiveTransitionSteps(curr, firstFrame, null);
    
    const transition = this._buildTransition(curr, firstFrame, transitionSteps);

    this.refJointPos = [...transition.jointPos, ...aligned.jointPos];
    this.refRootQuat = [...transition.rootQuat, ...aligned.rootQuat];
    this.refRootPos = [...transition.rootPos, ...aligned.rootPos];

    this.transitionLen = transition.jointPos.length;
    this.motionLen = aligned.jointPos.length;
    this.refIdx = 0;
    this.refLen = this.refJointPos.length;
    this.currentName = name;
    this.currentDone = this.refLen <= 1;
  }

  _buildPolicyToDatasetMap() {
    if (!this.datasetJointNames.length || !this.policyJointNames.length) {
      return null;
    }
    const datasetIndex = new Map();
    for (let i = 0; i < this.datasetJointNames.length; i++) {
      datasetIndex.set(this.datasetJointNames[i], i);
    }
    return this.policyJointNames.map((name) => {
      return datasetIndex.has(name) ? datasetIndex.get(name) : -1;
    });
  }

  _mapPolicyJointPosToDataset(jointPos) {
    if (!this.mapPolicyToDataset || !this.datasetJointNames.length) {
      return Float32Array.from(jointPos);
    }
    const out = new Float32Array(this.datasetJointNames.length);
    for (let i = 0; i < this.mapPolicyToDataset.length; i++) {
      const datasetIdx = this.mapPolicyToDataset[i];
      if (datasetIdx >= 0) {
        out[datasetIdx] = jointPos[i] ?? 0.0;
      }
    }
    return out;
  }
}

/**
 * Create a tracking helper with optimized settings for AI-generated motions
 */
export function createAIOptimizedTrackingHelper(config) {
  return new TrackingHelper({
    ...config,
    // Optimized defaults for AI-generated motions
    transition_steps: 120,           // Slightly longer default transition
    adaptive_transition: true,        // Enable adaptive transitions
    min_transition_steps: 40,         // Don't go below 40 steps
    max_transition_steps: 250,        // Don't exceed 250 steps
    pose_difference_threshold: 0.3,   // Lower threshold for triggering longer transitions
    high_difference_multiplier: 1.8,  // More aggressive multiplier
    enable_smoothing: false,          // Disabled by default - enable if motions are jittery
    smooth_window: 3,                 // Small smoothing window
    velocity_aware: true,             // Consider velocity in transitions
    velocity_scale_factor: 0.3        // Moderate velocity influence
  });
}

export default TrackingHelper;
