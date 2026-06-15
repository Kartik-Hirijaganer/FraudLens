# LinkedIn Post — Context Engineering

---

Everyone's talking about context engineering for AI agents.

But the hardest part of it, is getting the right information to the right consumer at the right time, and, it isn't a new problem. It's been showing up in distributed ML infrastructure for years.

Here's what I mean.

---

I recently read three engineering articles:

→ Netflix's ML Metadata Service (MDS) blog — built to let embeddings from the Studio team reach the Ads team, which couldn't happen because every domain had its own silo.

→ Google ADK's context architecture — read it a week back while looking into options to effectively manage multi-agent systems. Built so agents stop flooding sub-agents with irrelevant history from parent agents.

→ Andrej Karpathy's now-famous framing from June 2025: *"context engineering is the delicate art and science of filling the context window with just the right information for the next step."*

**Looking at the big picture, they're all solving the same problem. Just at different layers of the stack.**

Netflix's problem: the Studio team's content embeddings were potentially useful for Ads targeting and Personalization recommendations, but no infrastructure existed to share that knowledge cross-domain. The signal existed. The pipes didn't.

Multi-agent AI's problem: a root agent hands off to a sub-agent and dumps its entire conversation history along with it. The sub-agent now has to sift through 80% irrelevant context to do a 20% focused job.

Same root cause. **Right information, wrong recipient.**

---

What Netflix built (the Model Lifecycle Graph) is essentially what good multi-agent frameworks are converging toward:

| Problem | Netflix's Answer | Multi-Agent Answer |
|---|---|---|
| Siloed knowledge | Metadata graph connecting teams | Shared memory layer |
| Too much noise passed downstream | Structured entity references, not raw data | Scoped handoffs, not full history |
| On-demand retrieval vs always-on flooding | Agents query MDS when they need it | Agents call load_memory_tool only when relevant |
| Observability of what was shared | Event stream + AIP Portal | Pipeline processors + explicit transformations |

The engineering discipline is identical. The layer of the stack is different.

---

My learning:

The bottleneck is never the model. It's the plumbing.

When a sub-agent fails, the instinct is to improve the prompt. But usually, the agent failed because it got the wrong context, either too much, too stale, or from the wrong source entirely.

Context isn't a prompt concern. It's an infrastructure concern.

---

I wrote a deeper technical breakdown of how these patterns converge, with specifics from Netflix MDS, Google ADK, and what it means for how we architect multi-agent systems today.

Link in comments 👇
