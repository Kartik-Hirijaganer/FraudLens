# Changelog

All notable changes to FraudLens. Format follows Conventional Commits + SemVer.

## [0.5.0] - 2026-09-18

Release 0.5.0 makes the deterministic SAR quality gate part of the production path. Before this
release the single-writer path silently deleted fabricated citation IDs and returned a draft that
looked clean; the gate existed but nothing on the request path called it. The 1,000-case live matrix
measured what that hid — raw AWQ fabricated citations on 85 cases at concurrency 32 where BF16
fabricated none — and the release replaces silent repair with an explicit cascade: gate, escalate,
or fail with reason codes.

### Features

- Gate every SAR draft with the shipped deterministic evaluator before it is persisted or streamed,
  and escalate a citation-failed draft to the next model tier instead of repairing it
- Fail a cascade explicitly when every tier is exhausted, with PHI-free reason codes, rather than
  serving a plausible narrative; `SarQualityStatus` becomes a real tri-state driven by an evaluator
  that actually ran
- Route SAR drafting through named ordered profiles in production config, so a one-stage profile
  reproduces the pre-0.5.0 single-model behaviour exactly and the benchmark measures the same
  profiles the product runs
- Measure the shipped cascade rather than a benchmark-only client: a scenario invokes the production
  quality-gated drafter, and the raw quantization arms are single-stage scenarios of the same runner
- Publish a scenario-shaped gated-cascade report with mechanically derived headline, acceptance
  table, per-stage accounting, terminal-failure reason codes, and labelled comparisons
- Refuse to publish a cascade report unless the gate verdict recorded at request time equals the
  verdict re-derived from the persisted output on every attempt
- Capture the CUDA runtime version in endpoint provenance at run time, and publish it as absent
  rather than inferred when a run predates the capture

### Fixes

- Report cascade p95 and GPU-hours per case on the same row, and label every published comparison
  `same hardware` or `two endpoints vs one`, so a two-endpoint architecture result can never read
  as a free same-resource gain
- Exclude guided decoding from the published matrix after its latency canary measured a
  441.6-second p95 at concurrency 32, and record why rather than dropping the configuration
- Require a published AKS scaling load to clear a 95% served share or state its measured counts;
  the previous `succeeded > 0` bar let a 0.15% served rate read as a clean load test
- Give zero-cost local Kubernetes evidence a derived run id so it reconciles against the experiment
  ledger instead of being invisible to coverage
- Reject a typo'd `FRAUDLENS_LLM_*` environment variable at startup instead of ignoring it

### Removals

- Remove the direct model-only benchmark load runner and its raw HTTP client, superseded by the
  scenario runner once the full matrix measured its raw arms as single-stage scenarios and the
  verdict-parity criterion proved the two derivations identical
- Remove `SarInput.rag_context`, its producers, and `build_rag_context`: production had stopped
  rendering the pre-fenced block, so it was an unread second copy of the regulation text. The
  injection and PHI gates now assert the live prompt, where an uncommitted excerpt is refused
  outright rather than fenced
- Remove the `TransactionSummary` sample domain model and the empty `config/quality/` directory
- Lift the application-pass workload bounds out of source into benchmark config

### Documentation

- Add ADR-030 for the quality-gated SAR cascade: deterministic gate over LLM judge, quality tiers
  separate from transport fallback, one attempt per tier, explicit failure over degradation, and
  the carried decisions on escalation-tier surfacing, profile disposition, and the judged
  volume-encryption exception
- Link ADR-020 forward: its memory, latency, and throughput findings stand and were re-measured;
  its silence on grounding is what ADR-030 supersedes
- Distinguish the three runtimes in the README — the standing deployment, the ephemeral Kubernetes
  demonstration, and the ephemeral inference benchmark — so none is mistaken for another
- Disclose the AKS scaling-load caveat in the published artifact and the README
- Register the 0.5.0 claims against the published report, with no claim ahead of its evidence

## [0.4.0] - 2026-09-16

Release 0.4.0 takes FraudLens from a CI-validated deployment scaffold to a running one: a
permanent public URL on Azure Container Apps, a measured ephemeral AKS session, and the cost
controls that bound both. Every deploy remains human-approved.

### Features

- Deploy the backend to Azure Container Apps behind a required production approval, with
  build-once images, a revision staged at 0% traffic, and gated migration → authenticated smoke →
  promote-or-abort
- Serve the application at one public origin: the Vercel SPA proxies `/api/*` same-origin to the
  Container App, so no second hostname is published and CORS is a backstop rather than the
  mechanism
- Inject Container Apps secrets at deploy time from Infisical over OIDC — Terraform owns the app
  and the secret reference names, the deploy job owns the values, and neither reaches state,
  tfvars, logs, or artifacts
- Apply the validated AKS Terraform in one governed session and publish measured HPA and
  durable-worker evidence, then destroy the cluster and verify it clean
- Generate a dated Azure cost model from the committed Terraform shapes and live Azure Retail
  Prices, failing the build when a shape breaches the replica cap or the per-session ceiling
- Add budget alerts at two scopes — resource-group and an unfiltered subscription-wide guardrail —
  each notifying at 50 / 80 / 100% actual and 100% forecast
- Enforce hard spend caps that bound the bill rather than alerting on it: one maximum replica,
  0.1 GB/day log ingestion, a daily LLM ceiling, and manual-only scheduled jobs
- Add a daily read-only cost watchdog that fails its run when an AKS group survives or
  month-to-date cost passes its threshold, and a keep-warm ping that hides the cold start across
  weekday hours
- Refuse a deploy from a non-personal repository, commit identity, remote, or Azure account

### Fixes

- Remove the custom VNet from the Container Apps environment, which was provisioning a Standard
  Load Balancer and public IP as fixed infrastructure — roughly $22/month billed whether or not
  anyone visited
- Correct the AKS user-pool SKU to `Standard_D2as_v4` on regular priority: the `DASv5` family quota
  is zero in the target region and regional Spot capacity is below what the pool needs, so the
  original shape could not be created at any size
- Emit the gateway CORS origins as JSON rather than a comma-joined string, which the settings
  boundary could not decode and which crashed the container before it served a request
- Stop Terraform reverting blue/green promotion and injected secrets by ignoring the image,
  traffic weights, and secret collection on subsequent applies
- Cap Log Analytics ingestion with a daily quota so the workspace stops ingesting instead of
  billing on
- Cap live LLM spend with a fail-closed daily budget that returns the standard error envelope
- Cache the remote readiness probes so platform health checks stop making outbound provider calls
  on every invocation
- Consolidate the AKS workflow into one job so a single runner's address can be the API-server
  allowlist, with a wall-clock deadline, always-on teardown, and evidence uploaded even on failure
- Publish the AKS demonstration through a `LoadBalancer` Service and a matching network-policy rule
  so the session has an external path to exercise

### Documentation

- Record the executed AKS session in ADR-021 as a dated amendment covering the SKU and priority
  change, their causes, and the external-access addition
- Add ADR-029 for the recurring operational budget: ceiling, both budget scopes, the hard caps,
  the priced keep-warm window, the watchdog, the review cadence, and why the permanent URL runs on
  Container Apps while AKS stays ephemeral
- Move the AKS and deployment-topology claims to `demonstrated` against committed evidence, and
  register the cost-control claim
- Replace the "not applied" and "wired, validated, inert" runbook banners with the executed
  procedures, and document the request path, secret-delivery ownership, budget and cap behavior,
  and recovery
- Report both Kubernetes runtimes from their own artifacts, so kind evidence is never presented as
  an AKS deployment

## [0.3.0] - 2026-09-15

### Documentation

- Make RunPod the default GPU benchmark host
- Record durable execution decision
- Regenerate synthetic provenance contract
- Publish vLLM AWQ benchmark evidence
- Surface measured inference results
- Reconcile governed GPU experiment
- Record measured benchmark outcome
- Close release 0.3 implementation scope

### Features

- Add vLLM SAR provider route
- Add full-data temporal training pipeline
- Add vLLM AWQ benchmark harness
- Add governed Azure data batch runner
- Support direct vLLM process runtime
- Add governed RunPod GPU operator
- Add durable lease and fencing contract
- Enqueue durable investigations
- Execute queued investigations with leases
- Resume persisted investigation stages
- Add hardened deployment manifests
- Add local demonstration harness
- Complete pre-benchmark evidence gates
- Authorize synthetic benchmark provenance

### Fixes

- Close durable execution audit gaps
- Accept sparse lifecycle responses
- Preserve unique masked E2E accounts
- Make platform-dependent tests reproducible on clean checkouts

### Miscellaneous

- File-length gate, budget ledger, governance
- Single-source agent skills
- Allow benchmark host identifier

### Refactor

- Enforce 500-line module cap
- Isolate investigation runtime settings
- Isolate investigation event streaming

### Tests

- Add citation hallucination and egress gates
- Verify PostgreSQL lease concurrency
- Prove PostgreSQL worker recovery
- Enforce manifest and evidence contracts

## [0.2.0] - 2026-09-13

### Features

- Update backend configuration and documentation for Azure deployment; enhance code comments for clarity
- Implement local PR preflight check; add scripts for title validation and update documentation

### Miscellaneous

- Update changelog for v0.1.0
- Update version numbers to 0.1.0 across all packages and tests
- Forbid AI co-author attribution and add enforcement guard

## [0.1.0] - 2026-09-07

### Documentation

- Update README for clarity on local development and data handling; enhance instructions for running the app

### Features

- Migrate secrets management from Akeyless to Infisical
- Implement API health check and configuration management with tests
- Implement auto-filled PR description summary with area categorization
- Move PR title validation to a separate commitlint workflow for improved handling on edits
- Update CI workflows to support multiple branches and add merged branch deletion
- Enhance branch protection checks and update documentation for merged branch deletion
- Implemented deterministic ER diagram generation from SQLAlchemy models
- Implement FinCEN/BSA RAG index and retrieval system
- Implement SAR drafting system with budget guard and citation grounding
- Implement investigation API with endpoints for starting, retrieving, and streaming investigations
- Add repositories for alerts, analysis runs, and audit logs; enhance SAR draft management
- Implement models and functionality for investigations, alerts, and SAR PDF generation; enhance transaction history retrieval
- Introduce risk assessment and policy classes for enhanced fraud detection
- Implement LangGraph investigation pipeline with event handling, run management, and result persistence
- Add end-to-end test for triaging high-risk alerts, including SAR approval and resolution
- Implement dashboard metrics API and related models
- Add observability and security runbooks
- Add new components for model lifecycle, progress tracking, and SAR drafting
- Add ModelAdmin and Transactions pages with comprehensive tests
- Implement server-sent events (SSE) client and tests
- Add error handling and formatting utilities
- Add feedback components and tests for AsyncBoundary, ColdStartProgress, EmptyState, and Spinner; enhance routing and risk handling
- Release version 1.0.0 - first complete, locally-demoable release of the AML/fraud-investigation system
- Add source command skills for dead-code sweep, documentation regeneration, and pre-PR gate
- Enhance API routes with developer utilities and improve path parameter handling
- Add runtime configuration and developer utility models, enhance transaction ingest outcome
- Enhance transaction handling with PHI masking and access auditing
- Remove unnecessary interface exports from multiple components
- Update API routes to use camelCase for path parameters and enhance security documentation
- Enhance local development commands and update API path parameters for consistency
- Remove unnecessary interface exports from multiple components
- Remove unnecessary interface exports from multiple components
- Remove unnecessary interface exports from multiple components
- Remove unnecessary interface exports and enhance local demo commands
- Enhance integration tests for audit logging and transaction handling
- Update backend settings and error handling for local job execution and Azure integration
- Enhance Azure integration with managed identity and update documentation for job execution
- Enhance Azure integration with additional environment variables and local release checks
- Implement Azure integration with managed identity for job submission and storage, enhance local demo commands, and update documentation
- Add integration tests for Azure runtime backends and enhance local demo command handling
- Refactor image reference handling in workflows to use lowercase owner and improve caching
- Enhance alert handling by adding new statuses and updating related functions
- Update alert handling to include pending review status and enhance alert summary functionality
- Enhance alert schema with new statuses and update AlertTable component for improved display
- Add tests for DecisionRail and RiskDot components
- Add RiskDot and Timeline components for displaying risk indicators and activity lists
- Add DataTable component for rendering accessible tabular data with interactive rows
- Update ModelLifecyclePanel test to reflect AUC label change and add PageHeader test for rendering components
- Add tests for Pagination and SegmentedControl components
- Add DataTable and PageHeader components with initial tests
- Add SegmentedControl component for accessible segmented filters and update Select options type to ReadonlyArray
- Add StatTile component for rendering labeled metrics with optional hints
- Implement Pagination component for shared pagination footer in list screens
- Add formatAge function to render compact relative age from ISO timestamps
- Update AlertStatus type and add STATUS_LABELS for improved alert management
- Add severityRank function for sortable severity ranking and update riskTone documentation
- Update AlertDetail test to reflect action status changes and improve assertions
- Update button interactions in Alerts and Dashboard tests to use "Review" instead of "Open"
- Redesign Alerts and Investigation pages with new header and status filtering components
- Integrate new UI components in AlertDetail and ModelAdmin pages for improved layout and functionality
- Redesign Transactions page with new components and search functionality
- Redesign Dashboard page with new StatTile components for improved metrics display
- Add transaction filtering by risk band and implement search functionality in Transactions tests
- Enhance alert and dashboard metrics with new fields and status handling
- Add new alert status counts to metrics endpoint tests
- Update alert action handling in unit tests to include new statuses for PENDING_REVIEW and ESCALATED
- Add test for omitting heading in DecisionRail and label null risk as unscored in RiskDot
- Add tests for rendering Timeline items without optional body text and handling canary evaluation auto-aborts in ModelAdmin
- Redesign the analyst dashboard + app shell
- Add launch configuration and update app shell width for dashboard redesign
- Seed synthetic alerts and SAR drafts for demo dashboard population
- Refine dashboard chrome — short refs, model label, compact CTA, skeleton
- Redesign transactions page to dashboard chrome + pagination
- Server-side keyset pagination for transactions (total + search)
- Implement SAR draft regeneration service and associated components
- Add SAR regeneration endpoint for investigations
- Add endpoint to regenerate investigation SAR
- Add error specification for non-regenerable SAR drafts and update architecture documentation with SAR regeneration endpoint
- Remove ProgressSteps component and its associated tests
- Enhance SarStream to format markdown drafts and indicate regeneration state
- Add regenerate SAR endpoint and corresponding test
- Update investigation steps to CASE_STEPS and implement caseStepReady function
- Redesign investigation page to a guided five-step wizard and enhance SAR regeneration functionality
- Add regeneration functionality for SAR drafts and corresponding tests
- Enhance Tailwind configuration with auth theme and animations for login screen
- Enhance login functionality in tests with signIn and signOut integration
- Implement session gating in App component with login screen and sign-out functionality
- Enhance session management tests with DEMO_ROLES and rehydration scenarios
- Implement demo roles and session management with signIn/signOut functionality
- Add comprehensive tests for Login component including demo role selection and session management
- Implement Login component with demo role selection and session management
- Add auditor role and enhance permission management in demo setup
- Enhance permission management for alerts, investigations, and rules
- Update user role definitions and enhance training label descriptions
- Enhance transaction ingestion with permission checks and update training label source
- Add role-based error message and update user role description in documentation
- Implement demo role management in API client and enhance session handling
- Enhance error handling and permissions in session management and alert detail tests
- Implement role-based access control in session management and enhance dashboard user experience
- Update auth_dev_bypass_role options and enhance login test with demo role email and session validation
- Enhance login and investigation components with role-based permissions and improve test coverage for auditor sessions
- Implement role-based permissions for transaction ingestion and investigation in Transactions component
- Enhance investigation and model lifecycle tests with role-based permission checks and demo role overrides
- Update seed tests to reflect user count changes and add auditor role verification in auth tests
- Enhance _accept function to include role and user_id parameters; add test for auditor role permissions on rules
- Add JWKS reachability probe to readiness checks and implement async status fetching
- Implement user management endpoints and update settings for Supabase integration
- Add user identity API models and error specifications for user provisioning
- Add endpoints for retrieving current user and inviting users, update OpenAPI schema
- Implement Supabase Auth Admin API wrapper for user invites and update local dev documentation
- Add UserRepository for tenant-scoped user management and update Infisical secrets documentation for Supabase
- Integrate Supabase authentication and user management, update configuration and tests
- Add Supabase configuration to AppConfig for frontend integration
- Enhance session management with access token updates and session headers for SSE
- Add Supabase auth helpers tests and mock client for integration
- Enhance Login tests with Supabase sign-in flow and demo picker visibility checks
- Implement Supabase authentication client and enhance Login functionality
- Implement live environment setup for Supabase Auth and enhance seed process with bootstrap admin
- Add JWKS readiness checks to /readyz endpoint tests
- Update role claim in JWKS token verifier tests and add support for custom role claims
- Add tests for live environment configuration and command dispatch
- Add integration tests for user identity and admin invite APIs
- Add unit tests for Supabase custom-claims SQL
- Update .gitignore and pre-commit configuration for AML datasets and large file checks
- Add new dependencies for data processing and model training
- Add AML training dataset and configure data directory in settings
- Implement dataset fetching from Kaggle and update IEEE import logic
- Add Kaggle CLI dependency for dataset fetching and reorganize import statements in retrain script
- Enhance training scripts to support multiple data sources and add dataset manifest for provenance tracking
- Update gitleaks config and Makefile for IBM AML integration; enhance pytest options for live tests
- Add alert origin column to track provenance in alerts; update dependencies to include anyio
- Add DemoAgencySpec class and related configurations for deterministic demo agency handling; enhance AppSettings for candidate scoring and RAG embedding modes
- Enhance token verification to support trusted metadata for agency and role claims; add origin field to alert view
- Enhance investigations and pipeline wiring for alert handling and scoring pointer resolution; add support for alert review flags and improve session management
- Add origin to alerts during creation and implement update method for alert review flags; enhance transaction query for deterministic history
- Add AlertOrigin enum for alert provenance; implement get_for_run method in AlertRepository to retrieve agency alerts for investigation runs
- Add AlertOrigin to model exports and implement build_latest_candidate_pointer method for resolving the newest candidate version
- Add origin field to AlertView for alert provenance tracking; create __init__.py for RAG embeddings module
- Add alert_id field to InvestigationSnapshotResponse for tracking alerts raised during runs
- Enhance batch scoring with per-transaction fault isolation and limit on uninvestigated transactions
- Add origin field to Alert model for alert provenance tracking; enhance SarLlmConfig with reasoning effort hint
- Implement synchronous RAG embedder with LlmClient integration; enhance SupabaseAdminClient for user provisioning
- Add RAG embedding configuration file and update default.yaml for candidate scoring
- Add OpenAI text-embedding-3-small model configuration to catalog; update metadata for model performance metrics
- Add origin field to Alert model for provenance tracking; define AlertOrigin schema in OpenAPI
- Add provenance tracking to readiness probes; update ERD to include origin field in alerts
- Add GFP tenant-isolation benchmark commands and snapml dependency for x86-64
- Add GFP benchmark protocol configuration and documentation; establish graph-feature serving boundaries
- Update local development documentation; clarify prerequisites and command behaviors
- Add Architecture Decision Records (ADRs) documentation; index decisions and establish format
- Integrate research view with lazy loading and add sample data badge in alert queue
- Add sample data labeling for seeded alerts in AlertQueue and AlertTable tests; implement MotifGraph component for accessible SVG rendering
- Add GFP study data module and agency style for research page; enhance alert origin tracking in API
- Implement formatCompactRef utility for alert and investigation references; add tests for parseStudyData and layoutGraph functions
- Enhance AlertTable to display sample data badge for seeded alerts; add typed parser for GFP study data
- Add deterministic force-directed layout for motif graphs; integrate d3-force for stable positioning
- Add formatInvestigationRef function and update investigation state structure; enhance case step handling
- Enhance AlertDetail to display a badge for sample data alerts
- Update KPI label from "SARs filed" to "SARs approved" in Dashboard component
- Update routing for research graph typologies and enhance Investigation tests with new approval flow
- Enhance Login and AlertDetail tests with demo role handling and environment configurations
- Enhance Login component to support live demo authentication and improve demo role handling
- Add Research page for graph typologies and tenant isolation visualization
- Implement ResearchRoute component for graph typologies visualization
- Enhance Investigation and Transactions components for SAR approval flow and model management
- Add ModelRiskThresholds class and integrate into RiskPolicy for enhanced risk assessment
- Add streaming support and risk thresholds integration in adapters
- Enhance LLM client with streaming support and update investigation pipeline for alert enrichment
- Integrate alert handling and embedding provenance into pipeline and ingestion processes
- Enhance amount coercion with cent-quantization and add risk thresholds to model artifacts
- Enhance retriever with provenance checks and configurable similarity threshold; update feature extraction for new metrics
- Add comprehensive study on GFP typology lift and tenant-isolation gap
- Implement offline GFP tenant-isolation benchmark CLI and enhance dataset fetching with provenance tracking
- Enhance local demo orchestration with IBM AML data provisioning and add demo auth provisioning script
- Add frozen Pydantic boundary models for GFP tenant-isolation study
- Introduce DataSplit container and split_dataset function for deterministic dataset handling
- Implement deterministic edge builder and timestamp-cohort folds for GFP benchmark
- Add frozen per-arm metric contract and reference engine for offline GFP benchmark
- Add fake GFP engine and materialization/reporting models for offline benchmarks
- Add schema and scopes for global and per-tenant edge streams in GFP benchmark
- Add snapml integration and publication logic for GFP study
- Add context selection and target stratification for offline GFP benchmark
- Enhance integration tests for audit consistency and batch processing
- Add AlertOrigin to alert seeding and assertions in integration tests
- Add tests for model resolution and alert handling in investigations API
- Add synthetic training label fixtures and enhance migration tests for alert origin
- Add matured training labels to seeding in model lifecycle tests
- Add tests for latest candidate pointer resolution in model registry
- Enhance LLM integration tests with streaming generation and add demo auth reconciliation tests
- Add offline GFP tenant-isolation benchmark library and integration tests for SAR drafting
- Enhance integration tests for SAR drafter with streaming generation and new delta handling
- Add comprehensive unit tests for AML loader functionality and feature extraction
- Add AML mapping tests for channel and currency handling
- Add dataset-fetch tests for AML training plan and verification processes
- Add unit tests for JWKS token verifier handling of app metadata and user-editable claims
- Add unit tests for GFP boundary and report model validation
- Add comprehensive tests for GFP paired-benchmark orchestrator functionality
- Add unit tests for GFP edge-builder, reference engine, and scope-stream functionality
- Add unit tests for GFP schema and snapml adapter functionality
- Enhance OpenAI adapter with streaming support and add related tests
- Add tests for RAG retriever and enhance risk blend model thresholds functionality
- Add live-RAG embedder tests and enhance feature extraction assertions
- Add unit tests for Supabase Auth admin client functionality
- Enhance demo auth provisioning to support multiple tenants and improve UUID mirroring
- Update gfp-tenant-isolation-study.json with new metrics and motifs structure
- Update demo auth provisioning to support multiple tenants and improve UUID resolution
- Add tests for candidate scoring settings and amount quantization
- Update demo user display name and enhance Infisical secrets documentation for Vercel deployment
- Add favicon link to index.html and update tsconfig.include paths
- Add configPaths for Vite and update vite.config to include filesystem paths
- Update deploy-rollback documentation and add temporary favicon
- Enhance MotifGraph accessibility and add demo persona tests for production builds
- Refine demo picker logic and update documentation for clarity
- Update Makefile for clarity and remove deprecated demo.py module
- Add portfolio demo configuration options and update dev bypass claims logic
- Implement portfolio demo configuration endpoint and enhance alerts workflow
- Enhance user response model with display name and add fixture model label in registry
- Integrate portfolio demo routing and enhance batch scoring CLI for tenant-specific processing
- Add portfolio demo models and enhance risk policy loading with blend model weight
- Enhance CI workflows with portfolio demo checks and update Makefile targets
- Add portfolio demo reset workflow and initialize portfolio demo module
- Add portfolio demo bootstrap and ingest modules for transaction management
- Enhance user response model with display name and add calibration probe for portfolio demo
- Add portfolio demo configuration and verification modules for story validation
- Add portfolio demo configuration and alert workflow service for enhanced demo capabilities
- Add endpoint for reading portfolio demo configuration
- Add portfolio demo data provenance ADR and update OpenAPI schema for new properties
- Add portfolio demo route and configuration details to architecture documentation
- Update App component to use demo personas and enhance FraudGauge accessibility
- Add portfolio demo configuration and documentation for pipeline-produced state
- Enhance FraudGauge tests with dynamic labels and add RiskBandBar component tests
- Add portfolio demo tests and enhance router to support risk band filtering
- Integrate portfolio demo personas and enhance API client role handling
- Implement RiskBandBar component to display transaction risk bands
- Refactor AlertDetail tests to use demoPersona for sign-in
- Enhance API documentation and add PortfolioDemoConfig fetching functionality
- Enhance Dashboard and Investigation components with risk band and gauge label functionality
- Replace demo role handling with demoPersona in tests for improved clarity and consistency
- Enhance ModelAdmin and Research components with detailed feature contract explanations and improved readability
- Enhance ModelAdmin tests with single-hop served contract explanation and link to research study
- Enhance Transactions component with deep linking for risk filter and URL sharing
- Update documentation for default blend/banding policy in risk.py for clarity
- Add ADR-018 for portfolio demo data provenance and options for data generation
- Update agency references to clarify partitioning in aml_fraud.py and edges.py
- Refactor Transactions tests to use demoPersona for sign-in and enhance URL handling for risk-band filters
- Enhance local demo and provision_demo_auth scripts for portfolio demo integration and improved identity management
- Update agency index description in CuratedMotifNode and add research partitions for tenant isolation study
- Add portfolio demo identity and tenancy factories for behavioral tests
- Update seed script to use configured demo agency and personas for user seeding
- Update demo ingestion scripts to use configured agency and analyst IDs for training labels
- Update test files to use new demo user IDs and agency ID for consistency
- Update tests to use configured demo agency and bypass user ID for consistency
- Refactor tests to use centralized DEMO_AGENCY_ID for consistency
- Update tests to use centralized DEMO_AGENCY_ID and DEMO_ANALYST_ID for consistency
- Add integration tests for portfolio demo configuration and scoring behavior
- Add integration tests for portfolio-demo bootstrap and projection, ensuring proper behavior and security
- Update import_ieee script to allow explicit agency ID and enhance CLI documentation; refactor test to use dynamic agency ID
- Refactor tests to use dynamic agency ID and improve demo user handling in integration tests
- Update tests to use centralized DEMO_AGENCY_ID and DEMO_ANALYST_ID for consistency
- Refactor tests to use centralized DEMO_AGENCY_ID for consistency and add multi-tenant isolation tests
- Update authentication tests to use dynamic agency and user claims for improved consistency
- Add portfolio-demo literal guard and unit tests to prevent duplicated identity values
- Update email addresses in Supabase admin tests for consistency with new domain
- Add validation tests for portfolio demo story config loader
- Harden Supabase access and enhance alert details
- Revise README to enhance project overview and clarify functionality
- Enhance error handling and validation in alert workflow and Markdown component
- Add test for rendering compact detail in MetricCard and update ModelSelector test for version display
- Enhance MotifGraph component with improved edge and node labeling, and add new error descriptions
- Enhance AlertDetail tests with improved button labels and additional assertions for SAR workflow
- Add test for rendering technical SAR values in Markdown component and update MetricCard to include detail prop
- Enhance AlertDetail with new outcome options and improved SAR decision workflow
- Enhance tests for error handling, dashboard rendering, and research data presentation
- Enhance Dashboard and Research components with model version formatting and additional metrics display
- Add SAR evaluation commands and enhance dependencies for JSON schema validation
- Add agent executions table and enhance multi-agent workflow support
- Implement versioned prompt loader and enhance telemetry logging with agent and attempt fields
- Add deterministic review checks and evaluation function for SAR workflow
- Add multi-agent SAR drafting configuration and remove idempotency cache size setting
- Add bounded SAR agent configuration and workflow graph for multi-agent drafting
- Add mock agent team implementation and versioned prompt template for PHI-safe message assembly
- Implement tenant-scoped evidence tools for bounded SAR agent workflow with transaction history, rule hits, shap drivers, alert history, and regulation search capabilities
- Enhance investigation and alert APIs with agent execution details and workflow mode support
- Add enforce_rate_limit function and update dashboard response with agent cost details
- Introduce agent roles and execution status for bounded SAR workflow; enhance alerts and repository imports
- Implement idempotency key handling and workflow mode in analysis run repository; add agent execution repository for tenant-scoped persistence
- Add agent execution model and runtime configuration for tenant-aware multi-agent SAR workflow
- Add agent execution view and mapping function; enhance dashboard with agent cost metrics
- Add multi-agent SAR configuration and live agent fallback drafter; enhance agent drafting capabilities
- Enhance AlertDetailResponse with agent execution trace and workflow details; add error for LLM budget exceeded
- Enhance investigations and SAR models with workflow mode and regulation retrieval details; add agent execution trace and revision count
- Implement MultiAgentSarDrafter for bounded four-agent investigation; enhance prompt handling with versioned prompts
- Refactor SAR parsing functions and enhance SAR draft view with workflow and revision details; update configuration for multi-agent SAR
- Enable multi-agent SAR in demo and production configurations; add new SAR evaluation configuration file
- Enable multi-agent SAR configuration in dev and staging environments
- Update SAR model configuration to use Gemini 2.5 and adjust verification dates for models
- Add compliance reviewer, evidence investigator, and SAR writer prompt configurations for anti-money-laundering reviews
- Add ADR-019 for multi-agent SAR drafting and update README to include new ADR
- Add new schemas for agent execution and retrieved regulations in OpenAPI documentation
- Add multi-agent SAR drafting support and update tests for session gate
- Implement AgentTimeline component with multi-agent support and add Disclosure UI component
- Add Disclosure component tests and implement SAR evaluation data loader
- Enhance investigation module with multi-agent support and new data structures
- Enhance routing tests with multi-agent SAR and research graph typologies
- Add multi-agent SAR evaluation module with validation and parsing functions
- Integrate AgentTimeline component and enhance AlertDetail with SAR production timeline
- Enhance Investigation and Transactions components with new UI elements and improved state management
- Add SarEvalStudyRoute component and update pyproject.toml with jsonschema dependency
- Add ToolDefinition and ToolCall models with validation for tool invocations
- Enhance Anthropic and OpenAI adapters with tool handling and structured response support
- Implement fail-closed validation for model-requested tool arguments
- Add agent lifecycle events and claims support in SAR protocol
- Add benchmark script for multi-agent SAR evaluation and enhance seed configuration
- Add initial implementation of SAR evaluation support package with configuration and metrics
- Enhance SAR evaluation with agent event and claim support, and add publication validation
- Add SAR evaluation report and scenario generation modules
- Add integration tests for agent execution persistence and alerts API
- Implement real-API paired-arm harness for synthetic SAR evaluation and enhance test fixtures
- Enhance investigation API tests with new workflow mode restrictions and live profile readiness checks
- Add behavioral tests for tenant feature-flag matrix and fail-closed readers
- Add migration tests for agent execution constraints and downgrade functionality
- Add workflow and revision count to draft result in integration tests
- Add integration and unit tests for agent tools and configuration validation
- Add unit tests for agent prompts and tool call handling in LLM adapters
- Add unit tests for agent capabilities and tool validation in LLM security
- Add unit tests for agent lifecycle and fallback behavior in SAR processing
- Add unit tests for SAR evaluation CLI stages and provider separation
- Add behavioral tests for SAR evaluation scenarios and metrics
- Add behavioral tests for API orchestration and structured judging
- Expand analysis event type for multi-agent revisions and update pip version
- Align transaction text lengths with public ingest contract and optimize JWKS verifier retrieval
- Increase account and channel string lengths for transaction model and enhance error handling in SAR drafting
- Add calibration parameters for SAR evaluation typologies and enable research sharing in portfolio demo
- Enhance error handling in pipeline run and update portfolio demo model for research sharing
- Implement strict JSON schema validation for OpenAI adapter and enhance LLM client error handling
- Enhance judge stage with durable pre-call cost reservations and checkpointing
- Add migration tests for multi-agent event expansion and transaction text limits
- Enhance scenario calibration with new models and validation logic
- Update SAR writer configuration and enhance evaluation fact comparison logic
- Enhance output scanning tests and add provider error handling in SAR evaluation CLI
- Enhance SAR tests with budget tracking and strict schema validation
- Improve transaction search debounce logic and enhance test coverage for SAR evaluation scenarios

### Fixes

- Align queue rows — fixed-width severity badge + alert ref
- Decode keyset cursor as tz-aware UTC (Postgres pagination loop)
- Repair PR check failures
- Update vulnerable transitive dependencies
- Bound cross-platform training variance

### Miscellaneous

- Update documentation and governance references from Aegis to FraudLens across multiple files
- Update dependencies for vitest, jsdom, and coverage tools

### Refactor

- Update commitlint configuration and improve PR title validation
- Update navigation styles and improve accessibility
- Remove unnecessary interface declarations in options and risk modules

### Tests

- Fix pre-existing dashboard-metrics + sse test failures
- Update investigation button test to verify button text before click
