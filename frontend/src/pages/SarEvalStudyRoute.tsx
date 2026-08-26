/**
 * Summary: Lazy route boundary that binds the committed multi-agent SAR evaluation projection to
 * the presentational research page without making a backend or provider request.
 *
 * Key classes:
 * - (none)
 *
 * Key functions:
 * - (none) — the default export is route-only wiring.
 *
 * Notes:
 * - Keeping the static artifact import here ensures it is fetched only when the authenticated
 *   `#/research/multi-agent-sar` route is opened.
 */
import { sarEvalStudyData } from "../data/sarEvalStudy.data";
import { SarEvalStudy } from "./SarEvalStudy";

export default function SarEvalStudyRoute() {
  return <SarEvalStudy data={sarEvalStudyData} />;
}
