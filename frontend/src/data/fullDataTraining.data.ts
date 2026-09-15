/**
 * Summary: Optional build-time loader for the measured IBM full-data browser projection.
 *
 * Key classes:
 * - (none)
 *
 * Key functions:
 * - fullDataTrainingData: parsed aggregate evidence, or null until Phase 6 publication.
 *
 * Notes:
 * - A present artifact is parsed during module evaluation, so corrupt evidence fails the build.
 */
import { parseFullDataTrainingData, type FullDataTrainingData } from "../lib/fullDataTraining";

const artifacts = import.meta.glob("./ibm-full-data-training.json", {
  eager: true,
  import: "default",
});
const raw = artifacts["./ibm-full-data-training.json"];

export const fullDataTrainingData: FullDataTrainingData | null =
  raw === undefined ? null : parseFullDataTrainingData(raw);
