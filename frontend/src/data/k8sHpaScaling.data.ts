/**
 * Summary: Build-time loader for the measured Kubernetes scaling and durability evidence.
 *
 * Key classes:
 * - (none)
 *
 * Key functions:
 * - k8sHpaScalingData: strict, build-validated local kind evidence.
 *
 * Notes:
 * - The JSON is static and contains aggregate operational evidence only.
 */
import raw from "./k8s-hpa-scaling.json";
import { parseK8sHpaScalingData, type K8sHpaScalingData } from "../lib/k8sHpaScaling";

export const k8sHpaScalingData: K8sHpaScalingData = parseK8sHpaScalingData(raw);
