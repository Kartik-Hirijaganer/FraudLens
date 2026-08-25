/**
 * Summary: Build-time loader for the committed multi-agent SAR evaluation projection. The raw
 * JSON is statically imported and validated before the lazily loaded study page can render it.
 *
 * Key classes:
 * - (none)
 *
 * Key functions:
 * - sarEvalStudyData: the parsed, protocol-validated browser-safe study artifact.
 *
 * Notes:
 * - This module is imported only by SarEvalStudyRoute, so the artifact stays out of the base app
 *   chunk. A missing or drifted artifact fails the build; there is no backend fallback.
 */
import { parseSarEvalStudyData, type SarEvalStudyData } from "../lib/sarEvalStudy";
import raw from "./sar-multi-agent-study.json";

export const sarEvalStudyData: SarEvalStudyData = parseSarEvalStudyData(raw);
