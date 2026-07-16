/**
 * Summary: The route wrapper that binds the committed study data + the viewer's verified
 * agency to the presentational <Research/> page (GFP study Phase 7). It is the default
 * export the app lazily imports for `#/research/graph-typologies`, so the committed
 * artifact is only loaded when the research view is actually opened. The viewer's agency
 * comes from the session's verified `/me` agency id (mapped to a study agency index);
 * there is no client-selectable tenant and no backend call.
 *
 * Key classes:
 * - (none)
 *
 * Key functions:
 * - (none) — the default export is a route wrapper (default exports are not inventoried).
 *
 * Notes:
 * - A non-demo or absent agency resolves to `null`, and the page defaults the tenant view to
 *   the first agency; the app shell only renders this route for a signed-in session.
 */
import { Research } from "./Research";
import { studyData } from "../data/gfpStudy.data";
import { demoAgencyById, useSession } from "../lib/session";

export default function ResearchRoute() {
  const session = useSession();
  const agency = demoAgencyById(session?.agencyId);
  return <Research data={studyData} viewerAgencyIndex={agency ? agency.index : null} />;
}
