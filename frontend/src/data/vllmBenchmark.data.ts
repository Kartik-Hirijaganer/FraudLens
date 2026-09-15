/**
 * Summary: Optional build-time loader for the measured BF16-versus-AWQ browser projection.
 *
 * Key classes:
 * - (none)
 *
 * Key functions:
 * - vllmBenchmarkData: parsed aggregate evidence, or null until Phase 11 publication.
 *
 * Notes:
 * - A present artifact is parsed during module evaluation, so corrupt evidence fails the build.
 */
import { parseVllmBenchmarkData, type VllmBenchmarkData } from "../lib/vllmBenchmark";

const artifacts = import.meta.glob("./vllm-awq-sar-benchmark.json", {
  eager: true,
  import: "default",
});
const raw = artifacts["./vllm-awq-sar-benchmark.json"];

export const vllmBenchmarkData: VllmBenchmarkData | null =
  raw === undefined ? null : parseVllmBenchmarkData(raw);
