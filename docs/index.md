# What Should My Tax Agent Ask?

An AI intake agent that asks each tax client only what their return needs, then drafts the document request list for a preparer to approve. This page is the teardown: how it is built, what it measures, and what it found. **Draft: results are pending until the one preregistered test run.**

## Why measure the questions

Every tax season, preparers chase client documents. A generic organizer asks every client the same long list of questions, and whatever a client skips comes back later as a missing form and rework. AI organizers now build each client's questionnaire and document list from last year's data. Last year's data has two blind spots:

- **What changed this year.** A new job, a side business, Marketplace health coverage, a home sale. Last year's documents say nothing about these.
- **Rules that changed.** IRS FS-2025-05 ended several energy and vehicle credits before 2026 activity, so a list built from older knowledge can ask for documents that no longer matter.

Public tax benchmarks score question answering and return preparation, and one in development also scores requesting missing information. I found no public measurement of how many questions an intake agent asks or which documents it misses. That is the gap this project fills: the number of questions is the cost a client pays, and a missed document is the cost the preparer pays.

## Facts, rules, and the list

The agent's only job is to learn facts. It records each one with the client's own words as evidence. Code, not the model, turns facts into the document list.

```
agent (Claude Agent SDK)  --ask_client-->  client simulator (reads the household's truth)
        |
        +--record_fact(key, value, evidence)-->  MCP server "tax" (reads last year's profile only)
        +--build_request_list-->  rules table applied to recorded facts  -->  draft list
        +--submit_for_preparer_review-->  status pending_review (no send tool exists)
```

- **39 fact keys:** 36 top-level facts (filing status, number of employers, a side business, Marketplace coverage, and so on) plus 3 follow-ups asked only when their parent is yes (the year of a clean energy install, the year of efficiency upgrades, the date an EV was acquired).
- **33 rules:** 30 document requests and 3 traps for credits that ended. Each rule's condition is structured data (`eq`, `in`, `gte`, `lte`), never evaluated as code, and each cites an official source.
- **The list:** `build_request_list` applies the rules to the recorded facts. A fact that was never recorded counts as no (or zero), and the draft lists it under "not asked" for the preparer.

That split makes every miss traceable: either the conversation failed to learn a fact, or a rule was wrong.

Three guarantees live in code, not in the prompt:

1. **The model never writes the list.** `build_request_list` computes it from recorded facts and the rules. The model can only add a custom request, which is flagged for the preparer.
2. **Every fact needs evidence.** A hook in the harness accepts a fact only with a quote that appears verbatim in one of this conversation's client answers, or `prior_year:<field>` naming a field in last year's summary. It denies anything else and logs the denial. It checks that the quote exists, not that it supports the fact; wrong values are caught by scoring.
3. **Nothing can reach the client.** There is no send tool at all, rather than a denied one. The run ends with the draft in `pending_review`, and only the preparer sends.

The agent also cannot see the answer key: the MCP server is started with the profiles folder only, the truth files are read only by the simulator and the scorer, and the agent has no file tools.

## The rules and their sources

Generated from `rules/document_rules.yaml` by `uv run python -m eval.report index`. Every rule is `to_verify` until Roanuk verifies or excludes it; only verified rules count in test scoring. Source version and Verified on show what each rule was checked against and when, so the rules that cite drafts or 2025 editions can be checked again when final instructions are posted. Verified on reads "not yet" until Roanuk checks a rule, and "not verified (excluded)" for a rule dropped without being verified, as any rule still unverified at Block 9 is. The three T rules are traps: the credit ended, so no federal document is needed, and asking for one counts against the list.

<!-- rules-table:start -->
| ID | When | Document requested | Source | Source version | Status | Verified on |
|---|---|---|---|---|---|---|
| R001 | `w2_employer_count` >= 1 | Form W-2 from each employer (count from `w2_employer_count`) | [Topic no. 401, Wages and salaries](https://www.irs.gov/taxtopics/tc401) | guidance | to_verify | not yet |
| R002 | `self_employment` = yes | Business income and expense records, including any Forms 1099-NEC | [Manage taxes for your gig work](https://www.irs.gov/businesses/small-businesses-self-employed/manage-taxes-for-your-gig-work) | guidance | to_verify | not yet |
| R003 | `business_app_payments` = yes | Any Form 1099-K received | [Understanding your Form 1099-K](https://www.irs.gov/businesses/understanding-your-form-1099-k) | guidance | to_verify | not yet |
| R004 | `interest_income` = yes | Form 1099-INT from each payer | [Topic no. 403, Interest received](https://www.irs.gov/taxtopics/tc403) | guidance | to_verify | not yet |
| R005 | `dividend_income` = yes | Form 1099-DIV from each payer | [Topic no. 404, Dividends](https://www.irs.gov/taxtopics/tc404) | guidance | to_verify | not yet |
| R006 | `sold_investments` = yes | Form 1099-B or the consolidated brokerage statement | [Publication 550 (2025), Investment Income and Expenses](https://www.irs.gov/publications/p550) | guidance | to_verify | not yet |
| R007 | `digital_asset_transactions` = yes | Form 1099-DA if received, plus transaction records | [Digital assets (IRS.gov)](https://www.irs.gov/filing/digital-assets) | guidance | to_verify | not yet |
| R008 | `retirement_distribution` = yes | Form 1099-R | [Instructions for Forms 1099-R and 5498 (2026)](https://www.irs.gov/instructions/i1099r) | final_instructions | to_verify | not yet |
| R009 | `social_security` = yes | Form SSA-1099 | [Topic no. 423, Social Security and equivalent railroad retirement benefits](https://www.irs.gov/taxtopics/tc423) | guidance | to_verify | not yet |
| R010 | `unemployment` = yes | Form 1099-G | [Topic no. 418, Unemployment compensation](https://www.irs.gov/taxtopics/tc418) | guidance | to_verify | not yet |
| R011 | `state_refund_received` = yes and `itemized_last_year` = yes | Form 1099-G for the refund | [1099 Information Returns (All Other) (IRS FAQ: I received a Form 1099-G reporting the state tax refund I received during the year)](https://www.irs.gov/faqs/interest-dividends-other-types-of-income/1099-information-returns-all-other/1099-information-returns-all-other) | guidance | to_verify | not yet |
| R012 | `marketplace_coverage` = yes | Form 1095-A, Health Insurance Marketplace Statement | [Reconciling your advance payments of the Premium Tax Credit](https://www.irs.gov/individuals/reconciling-your-advance-payments-of-the-premium-tax-credit) | guidance | to_verify | not yet |
| R013 | `hsa_withdrawal` = yes | Form 1099-SA | [Instructions for Forms 1099-SA and 5498-SA (12/2026)](https://www.irs.gov/instructions/i1099sa) | final_instructions | to_verify | not yet |
| R014 | `has_mortgage` = yes | Form 1098 | [Instructions for Form 1098 (12/2026)](https://www.irs.gov/instructions/i1098) | final_instructions | to_verify | not yet |
| R015 | `bought_home` = yes | Closing statement | [Publication 530 (2025), Tax Information for Homeowners](https://www.irs.gov/publications/p530) | guidance | to_verify | not yet |
| R016 | `student_loan_interest` = yes | Form 1098-E | [Topic no. 456, Student loan interest deduction](https://www.irs.gov/taxtopics/tc456) | guidance | to_verify | not yet |
| R017 | `tuition_paid` = yes | Form 1098-T | [Education credits: AOTC and LLC](https://www.irs.gov/credits-deductions/individuals/education-credits-aotc-llc) | guidance | to_verify | not yet |
| R018 | `dependent_care_paid` = yes | Provider name, address, taxpayer ID, and amount paid | [2026 Instructions for Form 2441, Child and Dependent Care Expenses (draft, August 21, 2026)](https://www.irs.gov/pub/irs-dft/i2441--dft.pdf) | draft_instructions | to_verify | not yet |
| R019 | `new_dependent` = yes | The dependent's Social Security number and date of birth | [Checklist for free tax return preparation](https://www.irs.gov/individuals/checklist-for-free-tax-return-preparation) | guidance | to_verify | not yet |
| R020 | `k1_count` >= 1 | Schedule K-1 from each (count from `k1_count`) | [2026 Instructions for Schedule E (Form 1040), Supplemental Income and Loss (early release draft, posted 09/24/2026)](https://www.irs.gov/pub/irs-dft/i1040se--dft.pdf) | draft_instructions | to_verify | not yet |
| R021 | `rental_property` = yes | Rental income and expense records | [2026 Instructions for Schedule E (Form 1040), Supplemental Income and Loss (draft, September 2026)](https://www.irs.gov/pub/irs-dft/i1040se--dft.pdf) | draft_instructions | to_verify | not yet |
| R022 | `estimated_payments_made` = yes | Dates and amounts paid | [Form 1040-ES (2026), Estimated Tax for Individuals (Record of Estimated Tax Payments)](https://www.irs.gov/pub/irs-prior/f1040es--2026.pdf) | final_instructions | to_verify | not yet |
| R023 | `gambling_winnings` = yes | Form W-2G if received | [Topic no. 419, Gambling income and losses](https://www.irs.gov/taxtopics/tc419) | guidance | to_verify | not yet |
| R024 | `ip_pin_enrolled` = yes | This year's IP PIN | [Frequently asked questions about the identity protection personal identification number (IP PIN)](https://www.irs.gov/identity-theft-fraud-scams/frequently-asked-questions-about-the-identity-protection-personal-identification-number-ip-pin) | guidance | to_verify | not yet |
| R025 | `charitable_giving` = yes | Receipts and written acknowledgments | [Topic no. 506, Charitable contributions](https://www.irs.gov/taxtopics/tc506) | guidance | to_verify | not yet |
| R026 | `tips_income` = yes | Tip records and how the employer reported them | [Treasury, IRS issue final regulations listing occupations where workers customarily and regularly receive tips under the One, Big, Beautiful Bill (IR-2026-49, April 10, 2026)](https://www.irs.gov/newsroom/treasury-irs-issue-final-regulations-listing-occupations-where-workers-customarily-and-regularly-receive-tips-under-the-one-big-beautiful-bill) | guidance | to_verify | not yet |
| R027 | `overtime_pay` = yes | Overtime records and how the employer reported them | [Updates to questions and answers about the new deduction for qualified overtime compensation (FS-2026-13, August 2026; announced in IR-2026-88)](https://www.irs.gov/pub/taxpros/fs-2026-13.pdf) | guidance | to_verify | not yet |
| R028 | `new_vehicle_loan` = yes | Lender's interest statement | [Instructions for Form 1098-VLI (Rev. December 2026), for calendar year 2026](https://www.irs.gov/instructions/i1098vli) | final_instructions | to_verify | not yet |
| R029 | `hsa_direct_contribution` = yes | Contribution records, plus Form 5498-SA if received | [Instructions for Forms 1099-SA and 5498-SA (12/2026)](https://www.irs.gov/instructions/i1099sa) | final_instructions | to_verify | not yet |
| R030 | `sold_home` = yes | Closing statement, plus Form 1099-S if received | [Publication 523 (2025), Selling Your Home](https://www.irs.gov/publications/p523) | guidance | to_verify | not yet |
| T001 | `clean_energy_installed` = yes and `clean_energy_install_year` = 2026 | Nothing federal: the credit ended (trap) | [IRS FS-2025-05, FAQs for modification of sections 25C, 25D, 25E, 30C, 30D, 45L, 45W, and 179D under Public Law 119-21](https://www.irs.gov/newsroom/faqs-for-modification-of-sections-25c-25d-25e-30c-30d-45l-45w-and-179d-under-public-law-119-21-139-stat-72-july-4-2025-commonly-known-as-the-one-big-beautiful-bill-obbb) | guidance | to_verify | not yet |
| T002 | `efficiency_improvements` = yes and `efficiency_improvements_year` = 2026 | Nothing federal: the credit ended (trap) | [IRS FS-2025-05, FAQs for modification of sections 25C, 25D, 25E, 30C, 30D, 45L, 45W, and 179D under Public Law 119-21](https://www.irs.gov/newsroom/faqs-for-modification-of-sections-25c-25d-25e-30c-30d-45l-45w-and-179d-under-public-law-119-21-139-stat-72-july-4-2025-commonly-known-as-the-one-big-beautiful-bill-obbb) | guidance | to_verify | not yet |
| T003 | `ev_purchased` = yes and `ev_acquired_date` >= 2026-01-01 and `ev_acquired_date` <= 2026-12-31 | Nothing federal: the credit ended (trap) | [IRS FS-2025-05, FAQs for modification of sections 25C, 25D, 25E, 30C, 30D, 45L, 45W, and 179D under Public Law 119-21](https://www.irs.gov/newsroom/faqs-for-modification-of-sections-25c-25d-25e-30c-30d-45l-45w-and-179d-under-public-law-119-21-139-stat-72-july-4-2025-commonly-known-as-the-one-big-beautiful-bill-obbb) | guidance | to_verify | not yet |
<!-- rules-table:end -->

## Results against the baselines

**Pending.** Filled from the one test sweep (40 households, 3 trials each) after the bars and primary config are locked in `eval/prereg.yaml`. Until then, [results.md](results.md) holds the offline baseline numbers on the train and dev splits only.

| Config | What it is | Pass rate | Missed-document rate | Median questions | Cost per household |
|---|---|---|---|---|---|
| Primary config | chosen from dev results | pending | pending | pending | pending |
| B1 | full organizer: one question per fact key | pending | pending | pending | pending |
| B2 | prior year only: last year's documents, no questions | pending | pending | 0 | $0 |
| B3-haiku | Haiku 5.5 writes the list from the true facts | pending | pending | 0 | pending |
| B3-sonnet | Sonnet 5.5 writes the list from the true facts | pending | pending | 0 | pending |
| B3-opus | Opus 5.5 writes the list from the true facts | pending | pending | 0 | pending |

A run passes only if it misses no required document, adds at most one unnecessary request, records no fact that contradicts the truth, never claims the list was sent, and routes any tax advice question to the preparer.

## One model or one per step

**Pending** (Block 8, on dev). Does a Sonnet plan plus a Haiku conversation hold Sonnet's quality at closer to Haiku's cost?

| Config | Plans the questions | Runs the conversation | Pass rate (dev) | Cost per household (dev) |
|---|---|---|---|---|
| A-haiku | Haiku 5.5 | Haiku 5.5 | pending | pending |
| A-sonnet | Sonnet 5.5 | Sonnet 5.5 | pending | pending |
| A-opus | Opus 5.5 | Opus 5.5 | pending | pending |
| A-split | Sonnet 5.5, once | Haiku 5.5 | pending | pending |

## Findings I did not want

**Pending.** This section will list the results that went against the thesis, whichever way the test sweep comes out. The bars were locked before the test run so they cannot move to fit the results.

## What was deliberately not built

- **A send tool.** Not a denied tool: no tool exists that could send, email, or deliver anything to a client.
- **Reading uploaded documents.** Checking that an uploaded file is the document requested is future work (v1).
- **State returns and business returns.** Federal Form 1040 households for tax year 2026 only.
- **Tax advice.** The agent routes every advice question to the preparer with a note.
- **A web UI or hosted endpoint.** The MCP server runs locally over stdio. A hosted demo would spend the API credit per visitor and stop when it ran out.
- **Integration with any commercial tax product.**
- **MCP Sampling.** It is deprecated in the 2026-07-28 spec; the simulator calls the Messages API directly.

## Related work

- An Intuit Tax Pro Center article (Chase Vowell, December 19, 2025) describes AI organizers that build each client's questionnaire and document list from last year's data and follow up on missing items: https://accountants.intuit.com/taxprocenter/practice-management/putting-the-ai-into-tax-organizers/
- Columbia DAPLab's TaxBench, in development and updated July 2026, scores agents on preparing 2025 returns, including requesting missing information: https://daplab.cs.columbia.edu/benchmarks/taxbench/

## Limitations

- **The simulator is not a person.** A model plays each client from a fact sheet and a persona. Real clients misremember, contradict themselves, and volunteer things no persona captures.
- **The households are synthetic.** 100 households from one seeded generator, with obviously fake names. The mix of situations is a design choice, not a sample of any real practice.
- **Two edge cases are never generated:** a vehicle acquired by September 30, 2025 but placed in service in 2026 (which can still qualify for the clean vehicle credit), and a home EV charger (the 30C credit runs through June 30, 2026).
- **The Form 5695 carryforward.** Form 5695 still exists for 2026 to carry forward unused 2025 residential energy credit. The facts do not model unused credit, so the generator makes no household with a 2025 clean energy install, "Form 5695" stays a trap alias, and a real carryforward client would be scored wrong.
- **Scoring cannot catch a wrong rule,** because ground truth uses the same rules. Roanuk's blind labels on 30 households check the rules separately.
- **The evidence hook lives in the eval harness.** It would not exist in a Claude Desktop demo of the same server.
- **Some 2026 sources are drafts or 2025 editions.** Each rule shows its source version and the date it was verified, and is re-verified when final instructions are posted.

## How to run it

Requires Python 3.12 and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run pytest                                   # offline, network sockets disabled
uv run python -m agent.ping --dry-run           # starts the harness against a closed port; spends nothing
uv run python -m eval.baselines prior-year --split train --split dev   # baseline B2, no model
uv run python -m eval.report                    # writes docs/results.md from eval/runs/
uv run python -m eval.judge validate            # judge TPR and TNR against the labels
```

Live runs go through `eval/run.py`, and `uv run python -m eval.judge grade` judges any labeled run that has no verdict from the current judge model and prompt. They need an API key from a Claude Console workspace with its own spend limit, stored in `.env` as `TAX_AGENT_API_KEY`, and every run reserves its estimated cost in `ledger/spend.jsonl` before its first call. Don't set `ANTHROPIC_API_KEY`: Claude Code switches its own session to that variable when it finds it.

## Disclaimer

Not tax advice. All households are synthetic. Not affiliated with any tax software vendor.
