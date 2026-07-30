/**
 * Summary: The route wrapper that binds the committed study data to the presentational
 * <Research/> page (GFP study Phase 7). It is the default
 * export the app lazily imports for `#/research/graph-typologies`, so the committed
 * artifact is only loaded when the research view is actually opened. Research partitions are an
 * OFFLINE study concept, not runtime tenants, so the route passes no viewer index and the page
 * defaults its tenant view to the study's primary partition — the one the single runtime demo
 * agency mirrors. There is no client-selectable tenant and no backend call.
 *
 * Key classes:
 * - (none)
 *
 * Key functions:
 * - (none) — the default export is a route wrapper (default exports are not inventoried).
 *
 * Notes:
 * - `null` makes the page default the tenant view to the study's first partition; the app shell
 *   only renders this route for a signed-in session.
 */
import { Research } from "./Research";
import { studyData } from "../data/gfpStudy.data";

export default function ResearchRoute() {
  return <Research data={studyData} viewerAgencyIndex={null} />;
}
