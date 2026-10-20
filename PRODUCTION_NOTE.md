# Production Note: NovaMart Ops Agent on Google Cloud

## 1. Hybrid Architecture: Gemini Enterprise + Custom ADK Agent

Buy the commodity layers, build only what encodes NovaMart's rules.

| Layer | Gemini Enterprise (managed) | Custom ADK agent on Vertex AI Agent Engine |
|---|---|---|
| Users & UI | Employee SSO, chat UI, mobile access | n/a (registered as an agent in Gemini Enterprise) |
| Knowledge | Managed RAG over Drive / SharePoint / Confluence policy repositories | n/a |
| Transactions | n/a | OMS order lookup, inventory, price-match checks |
| Guardrails | Grounding, citations | Deterministic gatekeeper: $150 damage threshold, 24h lost-package rule, SOP-OPS-007 categories |

```
Store employee ─► Gemini Enterprise (identity, UI, policy RAG)
                     │  A2A / agent registration
                     ▼
                  ADK agent on Agent Engine ─► Apigee / PSC ─► OMS · POS · ServiceNow
```

Policy Q&A stays in Gemini Enterprise. Order questions route to the custom agent, where Python code, not the model, decides whether a ticket may be created, so the escalation boundary stays auditable and immune to prompt phrasing.

## 2. Connecting Real Client Data

- **Documents:** Vertex AI Search data stores with the layout parser and section-aware chunking replace the local BM25 index. Connectors sync incrementally and **inherit source ACLs**, so a policy draft restricted to managers never reaches an associate. Policy ID and effective date stay as metadata for citations.
- **Transactions:** The four tools become thin clients over NovaMart's OMS, inventory/POS, and ServiceNow (or Jira) APIs, exposed through **Apigee** (quotas, API keys, schema contracts) and reached privately over **Private Service Connect**. Ticket creation stays idempotent: the current duplicate check becomes an idempotency key on `order_id + issue_category`.

## 3. Security & Governance

- **Identity:** Workforce Identity Federation maps NovaMart's IdP to Google Cloud, and the employee's `store_id` comes from verified claims, not from chat text.
- **Least privilege:** One service account per tool. The agent can read orders and create tickets, but it cannot issue refunds.
- **PII:** Cloud DLP redacts customer phone numbers, addresses, and payment data from prompts, tool outputs, and logs before storage.
- **Model safety:** Model Armor screens inputs and outputs for prompt injection, jailbreaks, and data exfiltration, including instructions hidden in policy documents or order notes.
- **Audit:** Cloud Audit Logs and the ticket trail record who asked, what the agent saw, and why.

## 4. Evaluation & Quality Monitoring Over Time

- **CI/CD gate:** The two-layer suite in `evals/` (deterministic component checks plus golden agent cases) runs on every change to prompts, tools, or policies through the **Vertex AI Gen AI Evaluation Service** (tool-trajectory match, groundedness, instruction following). Merges are blocked on regression.
- **Tracing:** ADK OpenTelemetry spans go to Cloud Trace and **BigQuery** (tool calls, retrieved sections, verdicts).
- **Online sampling:** A daily LLM-judge pass on about 2% of conversations scores groundedness and hallucination rate, and low scores are routed to human review and new golden cases.
- **Business KPIs:** These are tracked in Looker:

| KPI | Definition | Target |
|---|---|---|
| Ticket deflection rate | Issues resolved in-store / all issues | ↑ |
| False-escalation rate | Tickets Operations closes as "store could resolve" | < 5% |
| Missed-escalation rate | Self-service answers later reopened as tickets | < 1% |
| Abstention precision | Out-of-scope questions correctly declined | > 98% |

Every policy document change re-indexes and re-runs the golden set before it reaches stores.
