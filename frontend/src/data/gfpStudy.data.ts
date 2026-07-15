/**
 * Summary: The single build-time load of the committed GFP tenant-isolation study visual
 * data (GFP study Phase 7). It statically imports the one committed artifact and validates
 * it through `parseStudyData`, so a missing or drifted artifact fails the build here rather
 * than rendering a broken or placeholder page (plan Phase 8). This module is imported ONLY
 * by the lazily-loaded research route, so the rest of the app (and its tests) never pull the
 * artifact in unless the research view is actually rendered.
 *
 * Key classes:
 * - (none)
 *
 * Key functions:
 * - studyData: the parsed, validated committed study payload.
 *
 * Notes:
 * - The JSON is produced by `make gfp-benchmark && make gfp-publish` (Phase 9); it is data,
 *   not code, and carries only redacted, aggregated, synthetic records.
 */
import { parseStudyData, type GfpStudyData } from "../lib/gfpStudy";
import raw from "./gfp-tenant-isolation-study.json";

export const studyData: GfpStudyData = parseStudyData(raw);
