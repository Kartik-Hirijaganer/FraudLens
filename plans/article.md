# Context Engineering Is an Infrastructure Problem. It Always Has Been.

*Getting the right information to the right consumer at the right time is not a new problem. Here's what happens when you look at it across two different stacks.*

---

In June 2025, Andrej Karpathy posted something that resonated across the entire AI engineering community: *"context engineering is the delicate art and science of filling the context window with just the right information for the next step."* Shopify CEO Tobi Lütke had said almost the same thing days earlier. By mid-2025, Gartner had declared the term mainstream.

Everyone nodded. Almost nobody unpacked it as an infrastructure problem.

The hardest part of context engineering — getting the right information to the right consumer at the right time — isn't a new problem. It's been showing up in distributed ML infrastructure for years. We just didn't connect the dots.

I recently read three engineering articles in close succession: Netflix's write-up on their ML Metadata Service, Google's architecture post on the Agent Development Kit (ADK) — which I came across a week back while looking into options to effectively manage multi-agent systems — and Nicolas Zeeb's multi-agent context engineering guide on Vellum. Karpathy's framing was already in my head as the connective thread.

What struck me wasn't any one article. It was the fact that looking at the big picture, all three were describing the same problem. Just at different layers of the stack.

---

## The Root Cause: Right Information, Wrong Recipient

Netflix's ML Metadata Service blog opens with a bluntly honest admission. By the time Netflix's ML investments had scaled across Personalization, Studio, Payments, and Ads, *"the models produced largely became black boxes."* Not because the models were bad. Because there was no infrastructure for sharing context across domains.

The Studio team had built sophisticated content embeddings that could identify scene boundaries, detect transitions, and understand content structure. Valuable stuff. But the Ads team couldn't use them for context-aware targeting, and the Personalization team couldn't use them for episodic recommendations. The knowledge existed. The plumbing didn't.

Now read this framing from Google's ADK engineering post on multi-agent systems:

> *"If a root agent passes its full history to a sub-agent, and that sub-agent does the same, you trigger a context explosion. The token count skyrockets, and sub-agents get confused by irrelevant conversational history."*

Different world — AI agents instead of ML teams — but the root cause is structurally identical. **Right information, wrong recipient.** Either nothing gets shared, or everything gets dumped. Both are failures of context architecture.

---

## The Naive Solutions (And Why They Break)

Both domains tried the obvious fix first: just make the container bigger.

For ML teams, that meant building more tooling per domain — more dashboards, more registries, more pipelines — and accepting that cross-team collaboration would be manual and messy.

For AI agents, that meant relying on ever-larger context windows. Throw enough tokens at the problem and maybe the model figures out what's relevant.

Google ADK's post is direct about why this doesn't hold:

> *"Model cost and time-to-first-token grow quickly with context size. 'Shoveling' raw history and verbose tool payloads into the window makes agents prohibitively slow and expensive."*

And there's an even subtler failure: signal degradation. A context window flooded with stale tool outputs and irrelevant logs doesn't just waste tokens — it actively degrades model judgment. The model starts fixating on past patterns instead of the immediate instruction. The technical term for this is "lost in the middle" — and it's a well-documented phenomenon across frontier models.

More space to paste text is not a scaling strategy. You have to change how context is *represented and managed*, not just how much of it you can hold.

---

## The Real Fix: Treat Context as Infrastructure

This is where both domains converge on the same architectural principle, even though they arrived at it independently.

### Netflix's Answer: The Model Lifecycle Graph

Netflix built the **ML Metadata Service (MDS)** — a system that ingests events from across their entire ML ecosystem (pipeline orchestrators, model registries, feature stores, experimentation platforms, dataset services) and materializes them into a unified, queryable graph.

The key design insight was separating *what exists* from *what any given consumer needs to see*.

A Studio team's content embedding doesn't get broadcast everywhere. Instead, MDS gives every entity a URI (`aip://model/registry/ranking-v5`) and makes it discoverable. An Ads engineer searching for context-relevant embeddings can find it. An automated system can query the graph to check model lineage or experiment associations. But nobody gets everything dumped on them by default.

The pattern at work here, borrowing from compiler theory, is: **storage is the source of truth; what any given consumer sees is a compiled view over that source.**

Netflix even calls out the failure mode they were avoiding: *"Without any discovery infrastructure, ML practitioners couldn't easily collaborate or share work across business verticals."* That's not a people problem. That's a context infrastructure problem.

### Google ADK's Answer: The Compiled Context View

Google ADK's architecture post makes the same compiler analogy explicit:

> *"Context is a compiled view over a richer stateful system. Sessions, memory, and artifacts are the sources. Flows and processors are the compiler pipeline. The working context is the compiled view you ship to the LLM for this one invocation."*

The ADK architecture separates context into four layers:

| Layer | What It Is | Analogy to Netflix MDS |
|---|---|---|
| **Working Context** | The immediate prompt for this model call | The query result — what this consumer sees right now |
| **Session** | Durable event log of the full interaction | Raw event stream from all ML systems |
| **Memory** | Long-lived, searchable knowledge | The model lifecycle graph — knowledge outliving a single run |
| **Artifacts** | Large binary/text objects, referenced not embedded | Feature store / dataset service — accessed by handle, not dumped |

The parallelism isn't coincidental. Both systems are solving the same problem: how do you give each consumer (ML practitioner or AI sub-agent) exactly the right slice of a much larger state space, without flooding them or starving them?

---

## A Side-by-Side Comparison

Here's how the specific patterns map:

| Problem | Netflix MDS Pattern | Multi-Agent ADK Pattern |
|---|---|---|
| **Siloed knowledge** | URI-based metadata graph connecting all ML entities | Shared memory layer (MemoryService) with vector search |
| **Too much noise downstream** | Structured entity references, not raw artifacts | Scoped handoffs — sub-agents get focused context, not full history |
| **On-demand retrieval** | Practitioners query MDS via AIP Portal when they need it | Agents call `load_memory_tool` or `load_artifact_tool` only when relevant |
| **Default = minimal, expand = explicit** | Lightweight references by default; `GET /api/v1/instances/{id}` to hydrate | Artifacts externalised — only loaded on agent request |
| **Preventing stale context** | Asynchronous enrichment with freshness timestamps | Context compaction — older events summarised, not carried verbatim |
| **Observability** | Event stream + AIP Portal graph traversal | Named pipeline processors + explicit transformation steps |
| **Cross-consumer consistency** | Normalised entity model (standardised URIs, fields) | Role translation on agent handoff — re-cast prior messages for new agent's POV |

The symmetry here is striking. Both systems land on: **minimal by default, explicit expansion on demand, durable source of truth separate from ephemeral views.**

---

## The Anti-Patterns They're Both Rejecting

Both blog posts spend meaningful time on what *not* to do. These failure modes are worth naming explicitly because they're the first things teams reach for.

**Context Dumping** — In multi-agent systems, this is passing the full conversation history to every sub-agent. In Netflix's ML world, this was teams trying to share context via Slack messages or shared spreadsheets — unstructured, unversioned, and invisible to automated systems. ADK calls this out directly with Artifacts: the fix for a 5MB CSV in your prompt history is to move it to an ArtifactService and give agents a handle, not the raw bytes.

**Siloed Black Boxes** — Netflix's Studio embeddings going unused by Ads isn't a business failure of imagination. It's a technical failure of discoverability. If an entity isn't addressable and searchable, it effectively doesn't exist to other consumers. The same is true of knowledge inside an AI agent that never gets externalised to a shared memory layer.

**Treating the Container as the Solution** — Bigger context windows and more ML tooling per domain both buy time. Neither changes the underlying architecture. As Google's post puts it: *"Throwing more tokens at the problem buys time, but it doesn't change the shape of the curve."*

---

## What This Means If You're Building Multi-Agent Systems Today

A few practical takeaways that fall out of this convergence:

**1. Design your memory layer before your agents.** The MemoryService is the MDS of your multi-agent system. It should be queryable, not just appendable. Agents should be able to search for relevant past context, not just inherit it from whoever called them.

**2. Externalize large state early.** If a tool output is going to be more than a few hundred tokens, it belongs in an artifact store — not in your session history. Your session should hold a reference and a summary, not the raw payload.

**3. Scope your handoffs intentionally.** When one agent transfers to another, the default should be *not* passing the full parent context. Build explicit rules for what crosses the boundary. In Google ADK, this is the `include_contents` setting. In your LangGraph system, it's the state schema you pass between nodes.

**4. Make your context transformations observable.** One of ADK's core principles is that context is built through named, ordered processors — not ad-hoc string concatenation. This is the same reason Netflix invested in a metadata pipeline with explicit event handlers per source system. When something goes wrong, you need to be able to trace exactly what context a consumer received and why.

**5. Don't blame the model when context is the real problem.** This is the most common mistake I see in production. An agent producing bad output often isn't failing because of model capability — it's failing because it received stale, conflicting, or irrelevant context. Fix the plumbing before you change the model.

---

## The Broader Pattern

There's a useful historical parallel here. In the early days of microservices, teams discovered that the hard problem wasn't writing individual services — it was managing the contracts and context between them. Service meshes, API gateways, and distributed tracing all emerged because "just add more services" wasn't enough. You needed infrastructure to manage what each service knew and when.

Multi-agent AI is going through the same maturation. The hard problem isn't building individual agents. It's managing what each agent knows, when it knows it, and how that knowledge moves across boundaries.

Netflix's ML Metadata Service is what happens when a company takes that problem seriously at the team and model level. Google ADK is what that looks like at the agent and call level. The engineering instincts are identical.

As Andrej Karpathy put it: *"In every industrial-strength LLM app, context engineering is the delicate art and science of filling the context window with just the right information for the next step."*

Context isn't a prompt concern. It's an infrastructure concern.

Build the plumbing first.

---

## References

- Hangfei Lin, *Architecting efficient context-aware multi-agent framework for production*, Google Developers Blog, Dec 2025
- Netflix Technology Blog, *Democratizing Machine Learning at Netflix: Building the Model Lifecycle Graph*, May 2026
- Nicolas Zeeb, *Best practices for building AI multi-agent systems*, Vellum, Dec 2025
- Andrej Karpathy [@karpathy], post on X, June 25, 2025
- Tobi Lütke [@tobi], post on X, June 19, 2025
- Lance Martin, LangChain, *Context Engineering for Agents*, 2024

---

*I'm an AI Engineer working on multi-agent healthcare systems. If this resonated — or if you've hit a different context failure mode in production — I'd genuinely like to hear about it in the comments.*