# Expense Validator design

Two designs cover rules compilation and expense validation:

| Design | Scope | Diagram |
|---|---|---|
| [Rules compilation](policy_compilation.md) | Policy documents → independent readings → review → approval → published ruleset | [HTML](offline_policy_compiler.html) · [PNG](offline_policy_compiler.png) |
| [End-to-end expense validation](expense_validation.md) | Receipts → preparation → classification → uncertainty and policy checks → source-verified results | [HTML](expense_validation.html) · [PNG](expense_validation.png) |

Compilation produces the ruleset consumed by validation. The restored compilation diagram preserves the original target architecture; its companion document distinguishes implemented behavior from planned capabilities. The end-to-end validation design describes the current package, including the monetary input contract and regression strategy.

The separate `backend/` and AWS/LocalStack service plan are outside this package design. Durable jobs, storage services, notifications, authentication, and ruleset activation are not package capabilities.

## Rendering

HTML diagrams open without a server or network connection. Export PNGs with the existing Chrome/Chromium renderer:

```sh
.venv/bin/python design/render_diagram.py
.venv/bin/python design/render_diagram.py design/expense_validation.html
```

Both use the default 1900 × 945 canvas. The renderer and its tests are shared tooling. `LICENSE-lucide` retains attribution for the original diagram assets.
