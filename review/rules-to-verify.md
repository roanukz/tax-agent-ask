# Rules to verify (Block 1)

Roanuk verifies or excludes each rule; only verified rules count in test scoring. Each source below was proposed by one researcher and then re-fetched by an independent skeptic who tried to refute it (2026-10-30). Seven first-choice sources were refuted and replaced with the skeptic's checked source; those are marked **Replaced**. Suggested wording is the skeptic's and has not been applied: the rules file keeps the PRD's document wording.

For each rule, reply in chat with one of: **verify**, **exclude** (with a reason), or **change** (what to change). Claude Code records the reply in `rules/document_rules.yaml` with `verified_by: roanuk` and the date.

| Rule | When | Source | Version | Skeptic |
|---|---|---|---|---|
| R001 | `w2_employer_count >= 1` | [Topic no. 401, Wages and salaries](https://www.irs.gov/taxtopics/tc401) | guidance | Supported |
| R002 | `self_employment = true` | [Manage taxes for your gig work](https://www.irs.gov/businesses/small-businesses-self-employed/manage-taxes-for-your-gig-work) | guidance | Supported |
| R003 | `business_app_payments = true` | [Understanding your Form 1099-K](https://www.irs.gov/businesses/understanding-your-form-1099-k) | guidance | Supported |
| R004 | `interest_income = true` | [Topic no. 403, Interest received](https://www.irs.gov/taxtopics/tc403) | guidance | Supported |
| R005 | `dividend_income = true` | [Topic no. 404, Dividends](https://www.irs.gov/taxtopics/tc404) | guidance | Supported |
| R006 | `sold_investments = true` | [Publication 550 (2025), Investment Income and Expenses](https://www.irs.gov/publications/p550) | guidance | Replaced |
| R007 | `digital_asset_transactions = true` | [Digital assets (IRS.gov)](https://www.irs.gov/filing/digital-assets) | guidance | Supported |
| R008 | `retirement_distribution = true` | [Instructions for Forms 1099-R and 5498 (2026)](https://www.irs.gov/instructions/i1099r) | final_instructions | Supported |
| R009 | `social_security = true` | [Topic no. 423, Social Security and equivalent railroad retirement [...]](https://www.irs.gov/taxtopics/tc423) | guidance | Supported |
| R010 | `unemployment = true` | [Topic no. 418, Unemployment compensation](https://www.irs.gov/taxtopics/tc418) | guidance | Supported |
| R011 | `state_refund_received = true` and `itemized_last_year = true` | [1099 Information Returns (All Other) (IRS FAQ: I received a Form [...]](https://www.irs.gov/faqs/interest-dividends-other-types-of-income/1099-information-returns-all-other/1099-information-returns-all-other) | guidance | Supported |
| R012 | `marketplace_coverage = true` | [Reconciling your advance payments of the Premium Tax Credit](https://www.irs.gov/individuals/reconciling-your-advance-payments-of-the-premium-tax-credit) | guidance | PRD source kept |
| R013 | `hsa_withdrawal = true` | [Instructions for Forms 1099-SA and 5498-SA (12/2026)](https://www.irs.gov/instructions/i1099sa) | final_instructions | Supported |
| R014 | `has_mortgage = true` | [Instructions for Form 1098 (12/2026)](https://www.irs.gov/instructions/i1098) | final_instructions | Supported |
| R015 | `bought_home = true` | [Publication 530 (2025), Tax Information for Homeowners](https://www.irs.gov/publications/p530) | guidance | Supported |
| R016 | `student_loan_interest = true` | [Topic no. 456, Student loan interest deduction](https://www.irs.gov/taxtopics/tc456) | guidance | Supported |
| R017 | `tuition_paid = true` | [Education credits: AOTC and LLC](https://www.irs.gov/credits-deductions/individuals/education-credits-aotc-llc) | guidance | Supported |
| R018 | `dependent_care_paid = true` | [2026 Instructions for Form 2441, Child and Dependent Care Expenses [...]](https://www.irs.gov/pub/irs-dft/i2441--dft.pdf) | draft_instructions | Replaced |
| R019 | `new_dependent = true` | [Checklist for free tax return preparation](https://www.irs.gov/individuals/checklist-for-free-tax-return-preparation) | guidance | Replaced |
| R020 | `k1_count >= 1` | [2026 Instructions for Schedule E (Form 1040), Supplemental Income and [...]](https://www.irs.gov/pub/irs-dft/i1040se--dft.pdf) | draft_instructions | Supported |
| R021 | `rental_property = true` | [2026 Instructions for Schedule E (Form 1040), Supplemental Income and [...]](https://www.irs.gov/pub/irs-dft/i1040se--dft.pdf) | draft_instructions | Replaced |
| R022 | `estimated_payments_made = true` | [Form 1040-ES (2026), Estimated Tax for Individuals (Record of [...]](https://www.irs.gov/pub/irs-prior/f1040es--2026.pdf) | final_instructions | Replaced |
| R023 | `gambling_winnings = true` | [Topic no. 419, Gambling income and losses](https://www.irs.gov/taxtopics/tc419) | guidance | Supported |
| R024 | `ip_pin_enrolled = true` | [Frequently asked questions about the identity protection personal [...]](https://www.irs.gov/identity-theft-fraud-scams/frequently-asked-questions-about-the-identity-protection-personal-identification-number-ip-pin) | guidance | Supported |
| R025 | `charitable_giving = true` | [Topic no. 506, Charitable contributions](https://www.irs.gov/taxtopics/tc506) | guidance | Supported |
| R026 | `tips_income = true` | [Treasury, IRS issue final regulations listing occupations where [...]](https://www.irs.gov/newsroom/treasury-irs-issue-final-regulations-listing-occupations-where-workers-customarily-and-regularly-receive-tips-under-the-one-big-beautiful-bill) | guidance | Supported |
| R027 | `overtime_pay = true` | [Updates to questions and answers about the new deduction for [...]](https://www.irs.gov/pub/taxpros/fs-2026-13.pdf) | guidance | Supported |
| R028 | `new_vehicle_loan = true` | [Instructions for Form 1098-VLI (Rev. December 2026), for calendar [...]](https://www.irs.gov/instructions/i1098vli) | final_instructions | Supported |
| R029 | `hsa_direct_contribution = true` | [Instructions for Forms 1099-SA and 5498-SA (12/2026)](https://www.irs.gov/instructions/i1099sa) | final_instructions | Replaced |
| R030 | `sold_home = true` | [Publication 523 (2025), Selling Your Home](https://www.irs.gov/publications/p523) | guidance | Replaced |
| T001 | `clean_energy_installed = true` and `clean_energy_install_year = 2026` | [IRS FS-2025-05, FAQs for modification of sections 25C, 25D, 25E, 30C, [...]](https://www.irs.gov/newsroom/faqs-for-modification-of-sections-25c-25d-25e-30c-30d-45l-45w-and-179d-under-public-law-119-21-139-stat-72-july-4-2025-commonly-known-as-the-one-big-beautiful-bill-obbb) | guidance | Supported |
| T002 | `efficiency_improvements = true` and `efficiency_improvements_year = 2026` | [IRS FS-2025-05, FAQs for modification of sections 25C, 25D, 25E, 30C, [...]](https://www.irs.gov/newsroom/faqs-for-modification-of-sections-25c-25d-25e-30c-30d-45l-45w-and-179d-under-public-law-119-21-139-stat-72-july-4-2025-commonly-known-as-the-one-big-beautiful-bill-obbb) | guidance | Supported |
| T003 | `ev_purchased = true` and `ev_acquired_date >= "2026-01-01"` and `ev_acquired_date <= "2026-12-31"` | [IRS FS-2025-05, FAQs for modification of sections 25C, 25D, 25E, 30C, [...]](https://www.irs.gov/newsroom/faqs-for-modification-of-sections-25c-25d-25e-30c-30d-45l-45w-and-179d-under-public-law-119-21-139-stat-72-july-4-2025-commonly-known-as-the-one-big-beautiful-bill-obbb) | guidance | Supported |

## R001

- **When:** `w2_employer_count >= 1`, count from `w2_employer_count`
- **Document:** Form W-2 from each employer
- **Client reason:** You were paid wages, so we need the year-end wage statement from each employer, including any job you left.
- **Source:** [Topic no. 401, Wages and salaries](https://www.irs.gov/taxtopics/tc401) (guidance)
- **Excerpt:** "You must include all income and withholding from all Forms W-2 you receive on your tax return."
- **Skeptic:** supported. Verified 2026-10-30: HTTP 200, reviewed 24-Sep-2026. This is general guidance and its dates are for tax year 2025. No final or draft 2026 Form 1040 instructions exist yet. For jobs that ended during the year, see the 2026 Instructions for Forms W-2 and W-3 (iw2w3).
- **2026 notes:** The 2026 General Instructions for Forms W-2 and W-3 (https://www.irs.gov/instructions/iw2w3, listed as the current revision on About Form W-2) add box 12 code TP (total cash tips reported to the employer), code TT (total qualified overtime compensation), and code TA (employer contributions to Trump accounts), and split box 14 into 14a and 14b, with 14b holding the Treasury Tipped Occupation Code(s). These feed the Public Law 119-21 deductions for qualified tips (up to $25,000) and qualified overtime (up to $12,500), which the 2025 Form 1040 instructions (https://www.irs.gov/instructions/i1040gi) say are claimed on the new Schedule 1-A. The same 2026 W-2 instructions raise the wage reporting [...]
- **Worth a look:**
  - Topic 401 is general guidance, not year-specific, and its date reference (February 2, 2026) is for tax year 2025. A reviewer wanting a year-specific cite could use the 2026 W-2 and W-3 instructions, but those are written for employers.
  - Keying on the activity is correct: the 2025 Form 1040 instructions say earnings must be reported even without a W-2. When a W-2 never arrives, Topic 154 says to contact the employer, call the IRS if still missing by the end of February, and use Form 4852 as a substitute, so intake should fall back to a final pay stub or other pay records.
  - Under the 2026 $2,000 threshold, an employer that withheld no income, social security, or Medicare tax and paid less than $2,000 (for example certain election workers) may not issue a W-2, yet the wages are still reportable.
  - For a joint return, the spouse's W-2s are needed too; confirm w2_employer_count covers both spouses, and note one employer can issue more than one W-2 or a corrected W-2c.
- **Suggested document wording (not applied):** Form W-2 from each employer (yours and, if filing jointly, your spouse's), including jobs that ended during the year, plus any corrected Form W-2c
- **Aliases:** W-2, Form W-2, W2, wage and tax statement, W-2c, corrected W-2
- **Your call:** 

## R002

- **When:** `self_employment = true`
- **Document:** Business income and expense records, including any Forms 1099-NEC
- **Client reason:** You had freelance or business income, so we need your income and expense records and any year-end payment statements from clients.
- **Source:** [Manage taxes for your gig work](https://www.irs.gov/businesses/small-businesses-self-employed/manage-taxes-for-your-gig-work) (guidance)
- **Excerpt:** "Collect and keep your records and receipts during the year."
- **Skeptic:** supported. Verified 2026-10-30: the gig page returns HTTP 200 (reviewed 28-Jun-2026) but covers only gig work and has some stale references. Prefer Gather your documents (HTTP 200, reviewed 26-Oct-2026) for general self-employment. Both pages are general guidance. The 2026 Form 1099-NEC threshold is $2,000 (i1099mec, Rev. 12/2026).
- **2026 notes:** The Instructions for Forms 1099-MISC and 1099-NEC (12/2026) (https://www.irs.gov/instructions/i1099mec, listed as the current revision on About Form 1099-NEC) raise the minimum reporting threshold to $2,000 for tax years beginning after 2025 (box 1a: enter nonemployee compensation of $2,000 or more), with possible inflation adjustment beginning in calendar year 2027. The 2026 Form 1099-NEC adds box 1b (cash tips), box 1c (Treasury Tipped Occupation Code), and box 1d (qualified overtime compensation) under Public Law 119-21 sections 70201 and 70202(a); the 2026 W-2 instructions (https://www.irs.gov/instructions/iw2w3) note that certain self-employed individuals may deduct up to $25,000 of [...]
- **Worth a look:**
  - The source page is about gig work, though it frames it as work done as an independent contractor (self-employed). For a traditional sole proprietor, a reviewer may prefer a general self-employment source; the Self-Employed Individuals Tax Center (fetched) covers Schedule C and the $400 rule but has no recordkeeping text, and year-specific [...]
  - Keying on the activity is correct: the same page says all income must be reported even without Forms 1099. Treat Form 1099-NEC as 'any received', since under the 2026 $2,000 threshold many small clients will not issue one, and app or card payments arrive on Form 1099-K (R003) instead.
  - Watch for double counting gross receipts across Form 1099-NEC, Form 1099-K, and the client's own records.
  - Gig work done as an employee is W-2 income (R001); confirm worker classification before requesting business records.
- **Suggested document wording (not applied):** Business income and expense records (income records, receipts, mileage logs, and bank or payment app statements), including any Forms 1099-NEC received
- **Aliases:** 1099-NEC, Form 1099-NEC, business income records, business expense records, self-employment records, Schedule C records, nonemployee compensation, business income and expenses, self-employment income
- **Your call:** 

## R003

- **When:** `business_app_payments = true`
- **Document:** Any Form 1099-K received
- **Client reason:** Customers paid you through an app or online marketplace, so we need any year-end statement it sent you.
- **Source:** [Understanding your Form 1099-K](https://www.irs.gov/businesses/understanding-your-form-1099-k) (guidance)
- **Excerpt:** "Whether or not you receive a Form 1099-K, you must still report any income on your tax return."
- **Skeptic:** supported. Verified 2026-10-30: HTTP 200, reviewed 28-Oct-2026. This is general guidance and states the $20,000 and 200-transaction threshold without a tax year. The year-specific cite is the Instructions for Form 1099-K (Rev. 12/2026). Consider widening the trigger to card processor payments and personal items sold at a gain.
- **2026 notes:** Public Law 119-21 retroactively restored the pre-2021 threshold, so payment apps and online marketplaces must file Form 1099-K only when gross payments exceed $20,000 and the number of transactions exceeds 200 (IR-2025-107, Oct. 23, 2025, https://www.irs.gov/newsroom/irs-issues-faqs-on-form-1099-k-threshold-under-the-one-big-beautiful-bill-dollar-limit-reverts-to-20000; restated on https://www.irs.gov/businesses/understanding-your-form-1099-k, reviewed 28-Oct-2026). The Instructions for Form 1099-K (12/2026) (https://www.irs.gov/instructions/i1099k, listed as the current revision on About Form 1099-K) repeat the $20,000 and 200 test and add box 1c (cash tips) and box 1d (Treasury Tipped [...]
- **Worth a look:**
  - The trigger covers only payment apps and online marketplaces. Clients whose customers pay by credit, debit, or gift card through a card processor get a Form 1099-K with no threshold, so consider widening the trigger.
  - The trigger says 'business payments', but the Understanding page says income from personal items sold at a gain is also reportable, and those sales can produce a Form 1099-K. Consider a trigger for selling personal items online.
  - 'Any received' is the right wording: below $20,000 and 200 transactions most sellers get no form, yet the income is still reportable, and R002 records cover it.
  - Reconcile with R002: Form 1099-K gross amounts include fees, refunds, and shipping, and can overlap with Form 1099-NEC or the client's own sales records.
- **Suggested document wording (not applied):** Any Form 1099-K received from a payment app, online marketplace, or card processor (collect it even if you think it is wrong)
- **Aliases:** 1099-K, Form 1099-K, payment app statement, payment card and third party network transactions
- **Your call:** 

## R004

- **When:** `interest_income = true`
- **Document:** Form 1099-INT from each payer
- **Client reason:** You earned interest, so we need the year-end interest statement from each bank or payer.
- **Source:** [Topic no. 403, Interest received](https://www.irs.gov/taxtopics/tc403) (guidance)
- **Excerpt:** "If you received payments of interest and/or tax-exempt interest of $10 or more, you should receive Copy B of Form 1099-INT or Form 1099-OID reporting those payments."
- **Skeptic:** supported. Source is guidance: Topic 403 (updated 07-Oct-2026, HTTP 200). The 2026 payer instructions exist only as a draft (Rev. December 2026), and Form 1099-INT stays on the January 2024 continuous-use revision. The $10 deposit-interest threshold is unchanged. Topic 403's $600 'other interest' figure is out of date (the draft says $2,000 for 2026).
- **2026 notes:** The 2026 issuer instructions exist only as a draft. Draft Instructions for Forms 1099-INT and 1099-OID (Rev. December 2026, posted 10/22/2026, https://www.irs.gov/pub/irs-dft/i1099int--dft.pdf) say in What's New that for tax years beginning after 2025 the reporting threshold for certain interest paid in the course of a trade or business rose to $2,000 (indexed for inflation starting in 2027). The $10 threshold for bank, credit union, and similar deposit interest is unchanged. The draft also says to keep using the January 2024 continuous-use revision of Form 1099-INT to report 2026 information in early 2027. About Form 1099-INT (https://www.irs.gov/forms-pubs/about-form-1099-int, updated [...]
- **Worth a look:**
  - Topic 403 was updated 07-Oct-2026 but still says other interest paid by a business is reported on Form 1099-INT if it is $600 or more, while the draft December 2026 instructions say $2,000 for 2026. The rule keys on the activity, so the trigger is not affected, but do not quote the $600 figure to clients.
  - Trigger wording: the rule should fire for any bank or credit union account that earned interest, not only when a form arrives. Payers do not have to issue a form for deposit interest under $10, but that interest is still reportable (Topic 403: report it 'even if you don't receive a Form 1099-INT'). The agent needs a fallback document, such as a [...]
  - Credit unions often call their payments 'dividends.' Topic 404 says dividends on credit union and similar deposit accounts are actually interest. Route 'credit union dividends' to this rule, not to R005.
  - 'Bank or credit union interest' is narrower than the interest the IRS expects reported. Treasury and savings bond interest, brokerage cash or sweep interest (often inside a consolidated statement), interest on federal or state tax refunds, tax-exempt bond interest, and original issue discount (Form 1099-OID) all need coverage. Confirm that other [...]
- **Suggested document wording (not applied):** Form 1099-INT from each bank or credit union (or the interest section of a consolidated statement); if none was issued, a year-end account statement showing interest paid for the year
- **Aliases:** 1099-INT, Form 1099-INT, interest income statement, bank interest statement
- **Your call:** 

## R005

- **When:** `dividend_income = true`
- **Document:** Form 1099-DIV from each payer
- **Client reason:** You received dividends, so we need the year-end dividend statement from each payer.
- **Source:** [Topic no. 404, Dividends](https://www.irs.gov/taxtopics/tc404) (guidance)
- **Excerpt:** "You should receive a Form 1099-DIV from each payer for distributions of at least $10."
- **Skeptic:** supported. Source is guidance: Topic 404 (updated 08-Oct-2026, HTTP 200). No 2026-specific final instructions exist. Form 1099-DIV is on the January 2024 continuous-use revision, and the only draft (Rev. 12-2027) applies to 2027 information. The $10 dividend threshold is unchanged.
- **2026 notes:** No final 2026-specific Form 1099-DIV exists. Form 1099-DIV and its instructions remain on the January 2024 continuous-use revision: About Form 1099-DIV (https://www.irs.gov/forms-pubs/about-form-1099-div, updated 13-May-2026) shows revision 0124 and recent developments 'None at this time,' and the HTML instructions (https://www.irs.gov/instructions/i1099div, reviewed 30-Apr-2026) are dated 01/2024. A draft December 2027 revision of the instructions (https://www.irs.gov/pub/irs-dft/i1099div--dft.pdf, posted 09/09/2026, for filing 2027 information in early 2028) says in What's New that beginning in tax year 2026 the reporting threshold for liquidation distributions changed from $600 to [...]
- **Worth a look:**
  - Trigger wording: the rule should fire on owning dividend-paying stocks or funds in a taxable account, not on receiving a form. Topic 404 says 'Even if you do not receive a Form 1099-DIV, you must still report all taxable dividend income.' Payments under $10 may not produce a form.
  - Dividends passed through from partnerships, S corporations, estates, or trusts come on Schedule K-1, not Form 1099-DIV (Topic 404). Confirm that a separate K-1 rule covers them.
  - Credit union 'dividends' are interest under Topic 404. Route them to R004, not here.
  - Dividends earned inside IRAs, 401(k)s, and similar retirement accounts are not reported on Form 1099-DIV. The intake agent should not ask for a dividend statement for retirement accounts (general knowledge, not taken from the cited page).
- **Suggested document wording (not applied):** Form 1099-DIV from each payer (or the dividend section of a consolidated brokerage statement); if none was issued, a year-end account statement showing dividends paid
- **Aliases:** 1099-DIV, Form 1099-DIV, dividend statement, dividends and distributions
- **Your call:** 

## R006

- **When:** `sold_investments = true`
- **Document:** Form 1099-B or the consolidated brokerage statement
- **Client reason:** You sold investments, so we need the year-end statement from your brokerage.
- **Source:** [Publication 550 (2025), Investment Income and Expenses](https://www.irs.gov/publications/p550) (guidance)
- **Excerpt:** "If you sold stocks, bonds, mutual funds, or certain commodities through a broker during the year, the broker should send you a Form 1099-B ... for each sale."
- **Skeptic:** researcher's source refuted (About Form 1099-B, Proceeds from Broker and Barter Exchange Transactions); replaced with the skeptic's checked source. Refuted as the primary source: About Form 1099-B (final 2026 form page, HTTP 200) is written for brokers and does not mention funds or consolidated statements. Use Pub. 550 (2025) as guidance. Keep the 2026 Instructions for Form 1099-B (final) and the draft 2026 Form 8949 (draft) as alternates, and use Pub. 1099 (2026) for the February 15 date and consolidated statements.
- **2026 notes:** The 2026 Form 1099-B and the Instructions for Form 1099-B (2026) are final. The About page (updated 21-Oct-2026) lists them as the current revision. Its recent developments list a correction dated 30-JAN-2026 to the 2025 and 2026 instructions (page 5, sales of precious metals). The 2026 Instructions for Form 1099-B (https://www.irs.gov/instructions/i1099b) say brokers must give the recipient a statement, and that digital asset sales in 2026 go on Form 1099-DA. They also say dual classification assets generally go on Form 1099-DA rather than 1099-B, with listed exceptions. Those instructions also exempt fractional share sales with gross proceeds under $20 from reporting. The 2026 [...]
- **Worth a look:**
  - The cited page is written for brokers: it says who must file Form 1099-B, not what the taxpayer must do. The taxpayer-side statement that every sale is reportable even without a form appears in the 2025 Form 8949 instructions and the draft 2026 Form 8949. The 2026 Form 8949 instructions were not posted as of 2026-10-30.
  - Trigger wording: the rule should fire on any sale, not on receiving a form. Some sales produce no Form 1099-B but are still reportable: fractional share sales with gross proceeds under $20, private or non-broker stock sales, and some foreign accounts. In those cases the client needs to supply purchase and sale records.
  - Crypto and other digital assets, including tokenized securities, are reported on Form 1099-DA, not Form 1099-B, starting with 2025 and 2026 sales. Confirm that a separate digital asset rule exists, and that 'sold stocks' wording does not pull in crypto or miss it.
  - The consolidated brokerage statement usually also contains the 1099-DIV and 1099-INT. Deduplicate requests with R004 and R005. Brokers have until February 15 to furnish 1099-B, and corrected statements often follow, so the agent may want to warn clients against filing early.
- **Suggested document wording (not applied):** Form 1099-B (or the broker's consolidated or substitute statement) from each brokerage or fund company, plus purchase and sale records for any sale not reported on a form or with basis not reported
- **Aliases:** 1099-B, Form 1099-B, consolidated brokerage statement, brokerage statement, consolidated 1099, consolidated Form 1099, brokerage 1099
- **Your call:** 

## R007

- **When:** `digital_asset_transactions = true`
- **Document:** Form 1099-DA if received, plus transaction records
- **Client reason:** You sold or traded digital assets, so we need any year-end statement from the platform and your own transaction records.
- **Source:** [Digital assets (IRS.gov)](https://www.irs.gov/filing/digital-assets) (guidance)
- **Excerpt:** "This reporting is required to be made on Form 1099-DA beginning with transactions on or after Jan. 1, 2025."
- **Skeptic:** supported. Verified 2026-10-30 against irs.gov/filing/digital-assets (reviewed 02-Sep-2026). Brokers report gross proceeds from 2025 and basis on certain transactions from 2026. Basis is required only for covered assets acquired after 2025 in a custodial account, so client records are still needed. Secondary support for reporting without a form: Understanding your Form 1099-DA page and FAQ Q108.
- **2026 notes:** 2026 is the first year of broker basis reporting. https://www.irs.gov/filing/digital-assets (reviewed 02-Sep-2026) says brokers must report gross proceeds for transactions effected on or after Jan. 1, 2025, and must report basis on certain transactions effected on or after Jan. 1, 2026. Instructions for Form 1099-DA (2026), https://www.irs.gov/instructions/i1099da (updated 30-Apr-2026, no draft label), say basis reporting is required only for covered securities: digital assets acquired after 2025 in an account where the broker provided custodial services. Assets acquired before 2026 are noncovered, and the broker does not have to report their basis. So a 2026 form may show proceeds with no [...]
- **Worth a look:**
  - Trigger wording: 'sold or traded' should also cover spending crypto on goods or services, swapping one digital asset for another, paying transaction fees with digital assets, and selling NFTs. The Understanding your Form 1099-DA page lists all of these as broker transactions.
  - Receiving digital assets as income (staking, mining, airdrops, wages or payment for services) is a separate reportable activity. Form 1099-DA covers dispositions, so that likely needs its own rule. The Form 1040 digital asset question also covers receipt.
  - The rule correctly keys on activity, not on receiving a form. IRS FAQ 108 (frequently-asked-questions-on-digital-asset-transactions) says income, gain, or loss must be reported regardless of the amount or whether a payee statement is received.
  - Basis will often be missing on 2026 forms (assets acquired before 2026, or moved in from another wallet or exchange), so the transaction-records part of the request matters most. Preparers should ask for acquisition date and cost for every lot sold.
- **Suggested document wording (not applied):** Form 1099-DA (if received), plus the client's own records for every digital asset sold, exchanged, spent, or used to pay fees: date and time acquired, cost basis, date disposed, and proceeds or fair market value in U.S. dollars
- **Aliases:** 1099-DA, Form 1099-DA, digital asset transaction records, crypto transaction records, cryptocurrency records, digital asset proceeds, crypto transaction, crypto exchange, cryptocurrency transaction, cryptocurrency exchange, digital asset transaction
- **Your call:** 

## R008

- **When:** `retirement_distribution = true`
- **Document:** Form 1099-R
- **Client reason:** You took money out of a retirement account or rolled one over, so we need the year-end distribution statement.
- **Source:** [Instructions for Forms 1099-R and 5498 (2026)](https://www.irs.gov/instructions/i1099r) (final_instructions)
- **Excerpt:** "You must report a direct rollover of an eligible rollover distribution."
- **Skeptic:** supported. Verified 2026-10-30 against the 2026 Instructions for Forms 1099-R and 5498 (final, reviewed 23-Jun-2026). Payers must report distributions of $10 or more and direct rollovers. Trustee-to-trustee transfers with no distribution are not reported, so client_reason should say 'rolled over', not 'moved'.
- **2026 notes:** Instructions for Forms 1099-R and 5498 (2026), https://www.irs.gov/instructions/i1099r, list these changes for 2026: separate address entry boxes; new box 7c (Trump account) and box 7d (earnings on excess contributions); box 8 renumbered to boxes 8a and 8b, with box 8a optional for 2026 for issuers of annuitized commercial annuity contracts; code Y in box 7a to identify a qualified charitable distribution; new Form 5498 code HP for repaying a qualified first-time home purchase distribution; and a note on qualified long-term care distributions. Trump accounts were created by Public Law 119-21, so a distribution from one would also produce this form. The same instructions say direct rollovers [...]
- **Worth a look:**
  - The chosen source is aimed at payers, not taxpayers. The taxpayer-facing pages fetched (Topic 410, Topic 413, and the Rollovers of retirement plan and IRA distributions page) do not name Form 1099-R. Topic 154 (tc154) is the clearest taxpayer page that refers to receiving a 1099-R, but it still shows the 2025-season date ('Feb. 2, 2026') for when [...]
  - The supporting excerpt covers rollovers. The withdrawal half (a distribution of $10 or more from retirement plans, IRAs, annuities, and pensions) is in the same instructions and on the About Form 1099-R page.
  - The instructions page has no draft label and the About page lists the 2026 revision as current, so it is marked final_instructions. A reviewer should still confirm it is the final 2026 release.
  - Trigger wording: 'retirement account withdrawal or rollover' may miss pension or annuity payments, RMDs, Roth conversions, qualified charitable distributions, plan loan offsets, corrective distributions of excess contributions, disability payments under insurance contracts, and the new Trump account distributions. Consider broadening it to 'any [...]
- **Suggested document wording (not applied):** Form 1099-R for each distribution, direct rollover, Roth conversion, or pension or annuity payment (RRB-1099-R for railroad retirement tier 2), plus proof of the redeposit date and amount for any indirect (60-day) rollover
- **Aliases:** 1099-R, Form 1099-R, retirement distribution statement, pension distribution statement, rollover statement
- **Your call:** 

## R009

- **When:** `social_security = true`
- **Document:** Form SSA-1099
- **Client reason:** You received Social Security, so we need the yearly benefit statement.
- **Source:** [Topic no. 423, Social Security and equivalent railroad retirement benefits](https://www.irs.gov/taxtopics/tc423) (guidance)
- **Excerpt:** "You should receive your Form SSA-1099, Social Security Benefit Statement"
- **Skeptic:** supported. Verified 2026-10-30 against IRS Topic 423 (reviewed 07-Oct-2026). Secondary support: the IRS FAQ on regular and disability benefits (reviewed 16-Oct-2026), which also says SSI is not included. Consider removing SSA-1042S, which is for nonresident alien filers outside Form 1040 scope.
- **2026 notes:** No change to the benefit statement itself was found for 2026. Topic 423 (https://www.irs.gov/taxtopics/tc423, reviewed 07-Oct-2026) says the statement arrives by early February. The IRS FAQ (https://www.irs.gov/faqs/social-security-income/regular-disability-benefits, reviewed 16-Oct-2026) says net benefits are in box 5, the taxable portion goes on Form 1040 line 6b, and replacements are available starting February 1 for the previous year. Public Law 119-21 added a $6,000 deduction for each individual age 65 or older for 2025 through 2028, phasing out above $75,000 of modified AGI ($150,000 joint), with an SSN required. Source: [...]
- **Worth a look:**
  - The ssa.gov pages returned HTTP 403 to the fetch tool, so the IRS Topic page is used. IRS is the preferred source anyway.
  - Trigger wording should include retirement, disability (SSDI), survivor, and spousal benefits, and benefits repaid during the year (Topic 423 says the form shows repayments). Supplemental Security Income (SSI) is a different program and is not reported this way; the rule should not request a statement for SSI alone.
  - Topic 423 says the form shows benefits paid to the person with the legal right to receive them. Benefits a parent receives for a child belong to the child, so the intake agent should ask whose name is on each statement. Spouses each get their own.
  - Railroad retirement tier 1 benefits come on Form RRB-1099 and nonresident aliens get Form SSA-1042S. Topic 423 mentions both. Consider whether they belong in this rule or in separate rules.
- **Suggested document wording (not applied):** Form SSA-1099 (Social Security Benefit Statement) for each person who received Social Security retirement, disability, survivor, or spousal benefits; Form RRB-1099 for railroad retirement Social Security equivalent benefits
- **Aliases:** SSA-1099, Form SSA-1099, Social Security benefit statement, Social Security Benefit Statement
- **Your call:** 

## R010

- **When:** `unemployment = true`
- **Document:** Form 1099-G
- **Client reason:** You received unemployment benefits, so we need the year-end statement from the state.
- **Source:** [Topic no. 418, Unemployment compensation](https://www.irs.gov/taxtopics/tc418) (guidance)
- **Excerpt:** "You should receive a Form 1099-G, Certain Government Payments, showing the amount of unemployment compensation paid to you during the year in Box 1"
- **Skeptic:** supported. Verified 2026-10-30: Topic 418 (HTTP 200, updated 24-Sep-2026) supports requesting Form 1099-G for unemployment and says all unemployment compensation is generally income, whether or not a form arrives. This is guidance. The final 2026 sources are Form 1099-G (Rev. December 2026) and its 12/2026 instructions. Suggested client_reason edit: 'You received unemployment benefits, so we need the year-end statement from each agency that paid them (usually your state unemployment office, or the [...]
- **2026 notes:** Form 1099-G is final as Rev. December 2026. Its instructions (12/2026, page updated 06-May-2026) say to use this revision to file 2026 information in early 2027. The Box 1 threshold is unchanged at $10 or more and includes Railroad Retirement Board unemployment payments (https://www.irs.gov/instructions/i1099g). New for this revision: Box 10 reports state paid family and medical leave benefits under Rev. Rul. 2025-4, and the state boxes are renumbered 11a, 11b, and 12 (https://www.irs.gov/instructions/i1099g, https://www.irs.gov/pub/irs-pdf/f1099g.pdf). The draft 2026 Schedule 1 (created 4/24/26) keeps line 7 for unemployment compensation, with a checkbox and an entry for repaid [...]
- **Worth a look:**
  - Keying the trigger on the activity is correct. The IRS unemployment compensation page says that if you got no Form 1099-G in the mail, you should find your payment amounts on your state unemployment agency website. The 2025 Form 1040 instructions say that if Box 1 is wrong, you report only the actual amount paid. The rule should fire whether or [...]
  - Topic 418 says you should receive Form 1099-G, but it does not itself say to report the income when no form arrives. That point comes from https://www.irs.gov/individuals/employees/unemployment-compensation, and that page still has stale wording ('To report unemployment compensation on your 2021 tax return'), so it was not used as the primary [...]
  - Identity theft: the 2026 Form 1099-G recipient instructions warn against reporting an incorrect Box 1 amount if you suspect identity theft. Intake should ask whether the client actually claimed the benefits and whether the amount looks right.
  - Scope: benefits from an employer-financed supplemental unemployment fund are not unemployment compensation. They are taxed as wages on Form W-2 (Pub 525). Private unemployment fund and union-paid benefits are reported on Schedule 1, line 8z, not line 7. State paid family leave now appears in Box 10 of the same Form 1099-G and should not be counted [...]
- **Suggested document wording (not applied):** Form 1099-G, Certain Government Payments, from each agency that paid unemployment (state unemployment agency or Railroad Retirement Board): Box 1 unemployment compensation and Box 4 federal income tax withheld
- **Aliases:** 1099-G, Form 1099-G, unemployment compensation statement, unemployment 1099-G, unemployment compensation
- **Your call:** 

## R011

- **When:** `state_refund_received = true` and `itemized_last_year = true`
- **Document:** Form 1099-G for the refund
- **Client reason:** You got a state or local tax refund after itemizing last year, so we need the statement showing the refund.
- **Source:** [1099 Information Returns (All Other) (IRS FAQ: I received a Form 1099-G reporting the state tax refund I received during the year)](https://www.irs.gov/faqs/interest-dividends-other-types-of-income/1099-information-returns-all-other/1099-information-returns-all-other) (guidance)
- **Excerpt:** "If you took an itemized deduction in an earlier year for taxes paid that were later refunded, you may have to include all or part of the refund as income on your tax return."
- **Skeptic:** supported. Verified 2026-10-30: the IRS FAQ (HTTP 200, updated 23-Sep-2026) supports taxing a state or local refund only when the client itemized and deducted that tax for the year that generated the refund. This is guidance. The 2026 Form 1040 instructions and the 2026 Pub 525 are not posted. The final 2026 Form 1099-G (Rev. December 2026) Box 2 and Box 3 recipient text agrees. Narrow the trigger to: refund (including credited, offset, or donated amounts) received in 2026 for a year in which the client [...]
- **2026 notes:** Form 1099-G is Rev. December 2026. Box 2 covers state or local income tax refunds, credits, or offsets of $10 or more, and Box 3 gives the tax year the refund was for. Payers need not send the recipient a copy if they can determine the recipient did not itemize for the year that gave rise to the refund (https://www.irs.gov/instructions/i1099g). The 2026 form's recipient instructions say a refund credited to estimated tax, offset against debts, or given to charity is still taxable if the tax was deducted (https://www.irs.gov/pub/irs-pdf/f1099g.pdf). Under P.L. 119-21 the SALT deduction is limited to $40,000 ($20,000 if married filing separately), subject to a modified AGI limitation but not [...]
- **Worth a look:**
  - The trigger wording 'itemized last year' is too narrow. The test is whether the client itemized and deducted state or local income tax for the tax year that generated the refund (Box 3), which can be earlier than last year, for example after an amended state return. A refund for another year sends the preparer to Pub 525 instead of the Form 1040 [...]
  - 'Refund received this year' should include amounts applied to 2026 estimated state tax, offsets against debts, and refunds donated through the state return. The 2026 Form 1099-G recipient instructions treat these as received and taxable if the tax was deducted.
  - Keying on the activity is correct. The 2025 Form 1040 instructions say to report any taxable refund even without a Form 1099-G, and they note the form may be available only electronically. States may also skip mailing it when they believe the client did not itemize.
  - Even when both facts are true, the refund may be partly or fully nontaxable. Examples: the client elected to deduct sales tax instead of income tax, the SALT limit capped the deduction, or the itemized total exceeded the standard deduction by less than the refund. The preparer needs the prior-year Schedule A or return as well as the refund [...]
- **Suggested document wording (not applied):** Form 1099-G from the state or local tax agency: Box 2 state or local income tax refunds, credits, or offsets (including amounts applied to estimated tax, offset, or donated) and Box 3 tax year of the refund; plus the prior-year return or Schedule A for that tax year if not on file
- **Aliases:** 1099-G, Form 1099-G, state refund 1099-G, state tax refund statement, 1099-G for state refund, state refund, state tax refund
- **Your call:** 

## R012

- **When:** `marketplace_coverage = true`
- **Document:** Form 1095-A, Health Insurance Marketplace Statement
- **Client reason:** You had a Marketplace plan, so we need the statement the Marketplace sends.
- **Source:** [Reconciling your advance payments of the Premium Tax Credit](https://www.irs.gov/individuals/reconciling-your-advance-payments-of-the-premium-tax-credit) (guidance)
- **Excerpt:** "Form 1095-A comes from the Marketplace and is used to complete Form 8962 (PRD section 3)."
- **Skeptic:** PRD source kept; researched alternate also supported: https://www.irs.gov/affordable-care-act/individuals-and-families/health-insurance-marketplace-statements. Verified 2026-10-30: the IRS Health Insurance Marketplace Statements page (HTTP 200, updated 14-Sep-2026) supports requesting Form 1095-A whenever anyone in the household enrolled through the Marketplace, and using it for Form 8962. This is guidance. The page still links 2025 instructions. The 2026 Form 1095-A PDF is final and has matching recipient instructions. Remove or demote the Form 8962, premium tax credit, and APTC aliases, and soften client_reason so 'settle' applies only when advance [...]
- **2026 notes:** P.L. 119-21 (Working Families Tax Cuts) removes the limit on repaying excess advance premium tax credit for tax years beginning after Dec. 31, 2025. Beginning with tax year 2026, the full excess must be repaid (https://www.irs.gov/tax-professionals/eitc-central/repayment-caps-eliminated-for-excess-advance-payments-of-the-premium-tax-credit, updated 07-Oct-2026). The Working Families Tax Cuts page lists PTC Sections 71301 to 71305 (https://www.irs.gov/newsroom/working-families-tax-cuts). PTC Q&A Q7 says the 2021 to 2025 expansion that removed the 400% of federal poverty line limit was temporary [...]
- **Worth a look:**
  - The trigger should be 'anyone in the tax household was enrolled in a Marketplace plan at any time in 2026', including state-based exchanges. Employer coverage, Medicaid or CHIP, Medicare, and plans bought directly from an insurer outside the Marketplace do not produce Form 1095-A and should not fire this rule.
  - Form 8962 is required only if Part III, column C shows advance payments or the client wants to claim the credit. That filing requirement applies even if the client is otherwise not required to file (2026 Form 1095-A recipient instructions and PTC Q&A). The Form 1095-A is still needed to make that determination, so requesting it on enrollment alone [...]
  - Keying on enrollment rather than receipt is correct. The PTC Q&A says to contact the Marketplace if the form is missing or wrong and notes clients may be able to view and download it, and a household member covered under another taxpayer's policy may need the shared-policy allocation (Form 8962 Part IV).
  - Collect a Form 1095-A for every policy, and use any CORRECTED version while ignoring VOID ones (2026 Form 1095-A instructions).
- **Suggested document wording (not applied):** Form 1095-A, Health Insurance Marketplace Statement, one for each Marketplace policy covering anyone in the tax household (use any CORRECTED form and disregard VOID forms); used to complete Form 8962
- **Aliases:** 1095-A, Form 1095-A, marketplace statement, Health Insurance Marketplace Statement, Health Insurance Marketplace statement
- **Your call:** 

## R013

- **When:** `hsa_withdrawal = true`
- **Document:** Form 1099-SA
- **Client reason:** You took money out of your health savings account, so we need the year-end distribution statement.
- **Source:** [Instructions for Forms 1099-SA and 5498-SA (12/2026)](https://www.irs.gov/instructions/i1099sa) (final_instructions)
- **Excerpt:** "File Form 1099-SA, Distributions From an HSA, Archer MSA, or Medicare Advantage MSA, to report distributions made from a health savings account (HSA)"
- **Skeptic:** supported. Confirmed: the final Instructions for Forms 1099-SA and 5498-SA (Revised 12/2026, HTTP 200) cover 2026 information using the April 2025 Form 1099-SA. The page is written for issuers; for the taxpayer side, cite the draft 2026 Form 8889 instructions (Jul 21, 2026) as an alternate until they are final.
- **2026 notes:** The Instructions for Forms 1099-SA and 5498-SA (Revised 12/2026, https://www.irs.gov/instructions/i1099sa) say to use the April 2025 revision of Form 1099-SA with these instructions to file and furnish 2026 information in early 2027, so this is the final source for tax year 2026. About Form 1099-SA (https://www.irs.gov/forms-pubs/about-form-1099-sa, last reviewed 30-Mar-2026) lists recent developments as "None at this time." The draft 2026 Instructions for Form 8889 (dated Jul 21, 2026, https://www.irs.gov/pub/irs-dft/i8889--dft.pdf) keep the rule that anyone who received HSA distributions in 2026 must file Form 8889, and line 14a still uses Form 1099-SA box 1. Under P.L. 119-21 and Notice [...]
- **Worth a look:**
  - The source is written for the account trustee (issuer), not the taxpayer. For plain taxpayer wording, Pub. 969 (2025) says "The trustee will report any distribution to you and the IRS on Form 1099-SA," but that edition is for 2025 returns.
  - Trigger wording: clients often don't think of debit card swipes or payments sent straight to a provider as "taking money out." Pub. 969 says total distributions include debit card payments, and the 1099-SA instructions say a distribution may be paid directly to a medical provider. Ask about card use explicitly.
  - A Form 1099-SA is also issued for distributions that pay qualified medical expenses, for withdrawals of excess contributions, and after the account holder's death. Trustee-to-trustee transfers are not reported. The rule should key on any money leaving the account, not on whether the client thinks it was taxable.
  - The same form covers Archer MSAs and Medicare Advantage MSAs. If the rule's trigger names only HSAs, it may miss those clients.
- **Suggested document wording (not applied):** Form 1099-SA from each HSA, Archer MSA, or Medicare Advantage MSA that paid out any money in 2026 (including debit card payments and payments sent straight to a provider)
- **Aliases:** 1099-SA, Form 1099-SA, HSA distribution statement
- **Your call:** 

## R014

- **When:** `has_mortgage = true`
- **Document:** Form 1098
- **Client reason:** You have a mortgage, so we need the year-end mortgage interest statement from your lender.
- **Source:** [Instructions for Form 1098 (12/2026)](https://www.irs.gov/instructions/i1098) (final_instructions)
- **Excerpt:** "Use Form 1098, Mortgage Interest Statement, to report mortgage interest (including points, defined later) of $600 or more you received during the year"
- **Skeptic:** supported. Confirmed: the final Instructions for Form 1098 (Revised 12/2026, HTTP 200) cover 2026 information using the April 2025 Form 1098. The page is written for issuers. The private-lender part of client_reason and the 2026 mortgage insurance rules depend on the draft 2026 Pub. 936 (Sep 30, 2026); recheck them against the final Pub. 936 and the final Schedule A instructions.
- **2026 notes:** The Instructions for Form 1098 (Revised 12/2026, https://www.irs.gov/instructions/i1098) say to use the April 2025 revision of Form 1098 with these instructions to file 2026 information in early 2027 and to furnish it to the borrower. About Form 1098 (https://www.irs.gov/forms-pubs/about-form-1098, last reviewed 07-Jun-2026) lists recent developments as "None at this time." Mortgage insurance premiums are new for 2026: the draft 2026 Pub. 936 (dated Sep 30, 2026, https://www.irs.gov/pub/irs-dft/p936--dft.pdf) says the itemized deduction for qualified mortgage insurance premiums is reinstated for tax year 2026 on Schedule A line 8d. It phases out above $100,000 AGI ($50,000 married filing [...]
- **Worth a look:**
  - The source is written for lenders (issuers). For plain taxpayer wording, Topic 505 says qualified mortgage interest and points are generally reported to you on Form 1098, and Pub. 936 says you will generally receive Form 1098 if you paid $600 or more of interest on any one mortgage. Both are guidance pages.
  - Trigger wording "Mortgage" is too vague. Key it on paying interest, points, or mortgage insurance premiums on a loan secured by a home during 2026 (first mortgage, refinance, home equity loan or line of credit, or co-op share loan), not on having a mortgage balance.
  - Form 1098 is required only when a lender in the course of its trade or business receives $600 or more on a mortgage. Seller-financed or private loans usually produce no form. Pub. 936 says to deduct that interest on Schedule A line 8b with the payee's name, address, and TIN. The rule should collect that information when no form arrives.
  - If the loan was sold or servicing was transferred during 2026, the client may get two or more Forms 1098 for one loan. Ask for all of them.
- **Suggested document wording (not applied):** Form 1098 from each lender or servicer that received 2026 mortgage interest, points, or mortgage insurance premiums on a loan secured by your home; for a private or seller-financed loan with no Form 1098, the lender's name, address, and TIN plus total interest paid
- **Aliases:** 1098, Form 1098, mortgage interest statement
- **Your call:** 

## R015

- **When:** `bought_home = true`
- **Document:** Closing statement
- **Client reason:** You bought a home, so we need the closing statement from the purchase.
- **Source:** [Publication 530 (2025), Tax Information for Homeowners](https://www.irs.gov/publications/p530) (guidance)
- **Excerpt:** "Generally, this information is included on the settlement statement you get at closing."
- **Skeptic:** supported. Source is Pub 530 (2025), labeled guidance; no 2026 edition or 2026 draft has been posted (the irs-dft draft is the 2025 edition). Pub 530 (2025) says the mortgage insurance deduction expired, but draft Pub 936 (2026) says it was reinstated for 2026, so cite both. Recheck when the 2026 Pub 530 and the 2026 Schedule A instructions are final.
- **2026 notes:** No 2026 Pub 530 exists yet: the HTML page says 'For use in preparing 2025 Returns' (last reviewed 30-Apr-2026), and the draft URL https://www.irs.gov/pub/irs-dft/p530--dft.pdf is also the 2025 edition. (1) Mortgage insurance premiums are deductible again for 2026: the 2026 draft Pub 936 (dated Sep 30, 2026, https://www.irs.gov/pub/irs-dft/p936--dft.pdf) says the itemized deduction for qualified mortgage insurance premiums 'has been reinstated for tax year 2026' (Schedule A line 8d), phased out for AGI over $100,000 ($50,000 MFS) and fully gone over $109,000 ($54,500 MFS). Prepaid private mortgage insurance paid at closing must be spread over the shorter of the mortgage term or 84 months, [...]
- **Worth a look:**
  - Source is the 2025 edition of Pub 530 (guidance); no 2026 edition or draft was available on 2026-10-30. Recheck when the 2026 Pub 530 and the 2026 Schedule A instructions are posted.
  - Pub 530 names the settlement statement and the HUD-1 but never the Closing Disclosure, which most mortgage closings now use. The intake agent should accept any of these names.
  - Trigger wording is right to key on the purchase, not on a lender form: a cash buyer gets no Form 1098 but still needs the closing statement for basis and the property tax split.
  - Points are fully deductible in the year paid only for a loan to buy or build a main home and only if the tests are met; second homes and rentals spread points over the loan. The rule may need a follow-up question about how the home is used.
- **Suggested document wording (not applied):** Closing statement from the home purchase (settlement statement, Closing Disclosure, ALTA statement, or HUD-1), including the buyer's side showing points, prorated property taxes, any mortgage insurance or VA funding fee paid at closing, and settlement costs
- **Aliases:** closing statement, Closing Disclosure, settlement statement, purchase closing statement, HUD-1
- **Your call:** 

## R016

- **When:** `student_loan_interest = true`
- **Document:** Form 1098-E
- **Client reason:** You paid student loan interest, so we need the year-end interest statement from your loan servicer.
- **Source:** [Topic no. 456, Student loan interest deduction](https://www.irs.gov/taxtopics/tc456) (guidance)
- **Excerpt:** "you should receive a Form 1098-E, Student Loan Interest Statement"
- **Skeptic:** supported. Source is Topic 456, labeled guidance (reviewed 24-Sep-2026, body still references tax year 2025); 2026 phaseout figures come from Rev. Proc. 2025-32 and the 2026 1098-E instructions are final. Add Pub 970 (2025) as the alternate that supports deducting interest not shown on a Form 1098-E. Recheck when the 2026 Pub 970 and Form 1040 instructions are posted.
- **2026 notes:** Rev. Proc. 2025-32 (https://www.irs.gov/pub/irs-drop/rp-25-32.pdf), section on Interest on Education Loans: for 2026 the $2,500 maximum begins to phase out for MAGI over $85,000 ($175,000 joint) and is fully phased out at $100,000 ($205,000 joint). For comparison, Pub 970 (2025) shows $85,000 to $100,000 and $170,000 to $200,000. The same Rev. Proc. says OBBBA (P.L. 119-21) section 70412 made permanent the section 127 rule that employer payments of student loan principal or interest count as tax-free educational assistance, with the $5,250 cap adjusted for inflation after 2026; Pub 970 (2025) says interest paid by an employer under such a program cannot be deducted. Instructions for Forms [...]
- **Worth a look:**
  - Trigger wording is correct to key on paying interest, not on receiving a form: a lender files Form 1098-E only when it receives $600 or more of interest, and Pub 970 says interest not shown on a Form 1098-E can still be deducted if it qualifies.
  - The $600 figure is the lender's reporting threshold, not a limit on the client's deduction; the agent should not tell a client with less interest that nothing is needed.
  - Clients with several lenders or servicers may get several forms; ask for all of them.
  - Eligibility gaps the intake should screen: married filing separately cannot claim it, a person claimed as someone else's dependent cannot claim it, and the taxpayer must be legally obligated on the loan (a parent paying a child's loan may not qualify).
- **Suggested document wording (not applied):** Form 1098-E, Student Loan Interest Statement, from each lender or servicer (or the lender's year-end statement or account summary showing interest paid, if no Form 1098-E was issued)
- **Aliases:** 1098-E, Form 1098-E, student loan interest statement
- **Your call:** 

## R017

- **When:** `tuition_paid = true`
- **Document:** Form 1098-T
- **Client reason:** You paid college tuition, so we need the tuition statement from the school.
- **Source:** [Education credits: AOTC and LLC](https://www.irs.gov/credits-deductions/individuals/education-credits-aotc-llc) (guidance)
- **Excerpt:** "To be eligible for an education credit, the law requires the student to have received Form 1098-T, Tuition Statement, from an eligible educational institution, domestic or foreign."
- **Skeptic:** supported. Verified 2026-10-30: IRS education credits guidance page (reviewed 12-Aug-2026) states the Form 1098-T requirement; its eligibility table still describes 2025 periods. The 2026 draft Form 8863 instructions (Sep 11, 2026) repeat the requirement and add the valid SSN rule. Recheck against the final 2026 Form 8863 instructions when posted.
- **2026 notes:** Final 2026 Form 8863 instructions are not out; the latest is a 2026 draft dated Sep 11, 2026 (https://www.irs.gov/pub/irs-dft/i8863--dft.pdf), and the final HTML instructions at https://www.irs.gov/instructions/i8863 are still the 2025 version. New for 2026 (draft 8863 What's New and footnote 3 on https://www.irs.gov/credits-deductions/individuals/education-credits-aotc-llc): beginning in tax year 2026 you must have a valid SSN (valid for employment and issued before the due date of the 2026 return, including extensions) to claim the AOTC or LLC; on a joint return only one spouse needs a valid SSN per the draft; a dependent student must also have a valid SSN. MAGI limits stay at $90,000 [...]
- **Worth a look:**
  - Trigger wording: the activity should be 'a student on the return was enrolled at an eligible postsecondary school (college, university, vocational or trade school)' rather than only 'paid college tuition'. Expenses paid by the dependent student or a third party are treated as paid by the taxpayer, and scholarships or grants shown on the form can [...]
  - The rule should not depend on the form arriving: the IRS says a credit may still be claimed without Form 1098-T if the school was not required to furnish it (qualified nonresident alien, expenses paid entirely by scholarships or a formal billing arrangement, noncredit courses) or failed to furnish it after a timely request; in that case ask for [...]
  - Box 1 of Form 1098-T may differ from what was actually paid; the intake should also request receipts or account statements, and for the AOTC receipts for required books and course materials bought outside the school.
  - The source page's eligibility table still describes 2025 academic periods; only its footnote 3 addresses 2026. Its footnote says 'You or your spouse (if married filing joint)' need a valid SSN, while the 2026 draft Form 8863 instructions say only one spouse needs one on a joint return; confirm against the final 2026 instructions.
- **Suggested document wording (not applied):** Form 1098-T, Tuition Statement, from each eligible college, university, or trade school for each student on the return (you, your spouse, or a dependent), plus receipts or account statements showing tuition, required fees, and course materials actually paid in 2026; if no Form 1098-T came, proof of enrollment, proof of payment, and a record of the request made to the school
- **Aliases:** 1098-T, Form 1098-T, tuition statement
- **Your call:** 

## R018

- **When:** `dependent_care_paid = true`
- **Document:** Provider name, address, taxpayer ID, and amount paid
- **Client reason:** You paid for care, so we need each provider's name, address, taxpayer ID number, and how much you paid.
- **Source:** [2026 Instructions for Form 2441, Child and Dependent Care Expenses (draft, August 21, 2026)](https://www.irs.gov/pub/irs-dft/i2441--dft.pdf) (draft_instructions)
- **Excerpt:** "Enter the total amount you actually paid in 2026 to the care provider. (Part I, line 1e; lines 1a to 1c ask for the provider's name, address, and taxpayer ID.)"
- **Skeptic:** researcher's source refuted (Child and Dependent Care Credit information | Internal Revenue Service); replaced with the skeptic's checked source. Refuted as sole source 2026-10-30: the guidance page (reviewed 15-Oct-2026) supports provider name, address, and TIN but not amount paid. Use the 2026 draft Form 2441 instructions (Aug 21, 2026), Part I lines 1a to 1e, labeled draft_instructions. Switch to https://www.irs.gov/instructions/i2441 once the final 2026 version posts.
- **2026 notes:** No final 2026 Form 2441 yet: https://www.irs.gov/forms-pubs/about-form-2441 (reviewed 20-Oct-2026) still lists the 2025 form and instructions as current. Drafts exist: 2026 Form 2441 (created 4/30/26, https://www.irs.gov/pub/irs-dft/f2441--dft.pdf) and 2026 instructions dated Aug 21, 2026 (https://www.irs.gov/pub/irs-dft/i2441--dft.pdf). Changes for 2026 (credit info page attributes them to the 'Working Families Tax Cuts'; draft instructions What's New): maximum credit rate rises from 35% to 50%; 50% at AGI of $15,000 or less, stepping down to 35%, then down again above $75,000 ($150,000 joint), never below 20%; the draft says the 20% floor is not reached until AGI exceeds $206,000 joint or [...]
- **Worth a look:**
  - Trigger wording: the credit applies only when care was for a qualifying person (dependent child under 13, or a spouse or dependent unable to care for themselves who lived with you more than half the year) and was paid so the taxpayer (and spouse if joint) could work or look for work; the intake question should capture that purpose, not just 'paid [...]
  - Form 2441 is also required when the client received employer dependent care benefits (W-2 box 10, or K-1 box 13 code O) even with no out-of-pocket credit claim; consider a separate trigger or an added question.
  - Payments to the taxpayer's spouse, the parent of the qualifying child, a dependent, or the taxpayer's own child under age 19 do not qualify; the intake should ask who the provider is.
  - Line 1e is the amount actually paid in 2026 regardless of when the care was incurred; 2025 expenses paid in 2026 use a separate worksheet and 2026 care prepaid or paid in 2027 is excluded from line 2, so the client should break out payments by period.
- **Suggested document wording (not applied):** For each care provider: name, address, taxpayer identification number (SSN or ITIN for an individual, EIN for a business; name and address only for a tax-exempt organization), whether the provider was your household employee in 2026 (for example a nanny in your home), and the total amount actually paid to that provider in 2026 (Form 2441 Part I, lines 1a to 1e; Form W-10 can be used to collect it). Also any dependent care benefits received from [...]
- **Aliases:** dependent care provider information, care provider information, child care provider information, provider taxpayer ID, Form W-10, child care receipts, dependent care receipts, childcare provider, child care provider name and taxpayer ID, child care provider, dependent care provider, child care expenses, dependent care expenses, daycare
- **Your call:** 

## R019

- **When:** `new_dependent = true`
- **Document:** The dependent's Social Security number and date of birth
- **Client reason:** You have a new dependent, so we need a copy of their Social Security card and their date of birth.
- **Source:** [Checklist for free tax return preparation](https://www.irs.gov/individuals/checklist-for-free-tax-return-preparation) (guidance)
- **Excerpt:** "Social Security cards for you, your spouse and dependents; Birth dates for you, your spouse and dependents on the tax return."
- **Skeptic:** researcher's source refuted (Dependents | Internal Revenue Service (IRS FAQ)); replaced with the skeptic's checked source. Refuted as sole source 2026-10-30: the IRS Dependents FAQ (reviewed 16-Oct-2026) supports the SSN requirement and its deadline but not date of birth. The IRS free tax prep checklist (reviewed 24-Oct-2026, guidance) lists both dependents' Social Security cards and birth dates. Keep the FAQ as a secondary reference for SSN timing and ATINs.
- **2026 notes:** Final 2026 Form 1040 instructions are not posted; https://www.irs.gov/instructions/i1040gi is still the 2025 version ('You must enter each dependent's social security number (SSN)'). The 2026 draft Form 1040 (created 8/19/26, https://www.irs.gov/pub/irs-dft/f1040--dft.pdf) dependents section has columns for first name, last name, SSN, relationship, lived with you more than half of 2026 and in the U.S., full-time student or permanently and totally disabled, and credit type; it has no date of birth column. The 2026 draft Schedule 8812 instructions dated Aug 18, 2026 (https://www.irs.gov/pub/irs-dft/i1040s8--dft.pdf): CTC up to $2,200 and ACTC up to $1,700 per qualifying child; the qualifying [...]
- **Worth a look:**
  - The primary FAQ supports the SSN requirement for a newborn but does not mention date of birth; the date of birth element is supported by the IRS VITA checklist ('Birth dates for you, your spouse and dependents on the tax return'), which is general and not specific to new dependents. Date of birth is needed for age tests (CTC under 17 at end of [...]
  - Trigger wording: 'new dependent' should include a birth, an adoption or adoption placement, a foster placement, and a relative who newly qualifies (for example a parent moving in), not only newborns; each needs a TIN and supporting facts such as months lived with the taxpayer.
  - An SSN is required for every dependent listed, not only new ones; the rule assumes prior-year dependents are already on file, which a reviewer should confirm.
  - For pending adoptions the FAQ points to an ATIN (Form W-7A), which expires 2 years after issue and supports only the credit for other dependents, not the CTC/ACTC; the intake should record which identifier the client has.
- **Suggested document wording (not applied):** A copy of the new dependent's Social Security card (name and number as printed) and their date of birth; if the SSN has not been issued, the date it was applied for, or an ATIN (Form W-7A) for a pending adoption, or an ITIN for a dependent who cannot get an SSN; for a child born and died in 2026 without an SSN, a birth certificate, death certificate, or hospital record showing live birth
- **Aliases:** dependent Social Security number, dependent SSN, dependent date of birth, Social Security card for dependent, new dependent information, Social Security number, Social Security card, SSN, date of birth
- **Your call:** 

## R020

- **When:** `k1_count >= 1`, count from `k1_count`
- **Document:** Schedule K-1 from each
- **Client reason:** You have a share in a partnership, company, estate, or trust, so we need the year-end statement from each one.
- **Source:** [2026 Instructions for Schedule E (Form 1040), Supplemental Income and Loss (early release draft, posted 09/24/2026)](https://www.irs.gov/pub/irs-dft/i1040se--dft.pdf) (draft_instructions)
- **Excerpt:** "You should receive a Schedule K-1 from the partnership or S corporation."
- **Skeptic:** supported. Verified 2026-10-30 (HTTP 200). The 2026 early release draft of the Schedule E instructions (posted 09/24/2026) says in Part II that you should receive a Schedule K-1 from the partnership or S corporation (and possibly a K-3), and in Part III a Schedule K-1 (Form 1041) from the fiduciary. Label: draft_instructions. Switch to the final 2026 Schedule E instructions once posted; the HTML at irs.gov/instructions/i1040se still shows 2025.
- **2026 notes:** Only a 2026 draft exists. The final 2026 Schedule E instructions are not posted. The stable HTML page https://www.irs.gov/instructions/i1040se still shows the 2025 instructions (last reviewed 30-Apr-2026) with the same K-1 sentences. The 2026 draft (https://www.irs.gov/pub/irs-dft/i1040se--dft.pdf) adds new wording to Part II: if the partnership or S corporation has items related to international taxes, 'you may also receive a Schedule K-3.' Part III still says 'You should receive a Schedule K-1 (Form 1041) from the fiduciary.' Both parts still say to report your share 'even if not received.' The draft also says estimated taxes credited to a beneficiary by a trust (Schedule K-1 (Form 1041), [...]
- **Worth a look:**
  - The source is a draft PDF. WebFetch could not read its text (compressed fonts), so I decoded the downloaded file myself to confirm the excerpt. The IRS draft cover sheet says drafts stay posted at IRS.gov/DraftForms after the final is released. Once the 2026 final is posted, consider switching to the stable HTML page [...]
  - The trigger should be holding an interest (or being a beneficiary) at any time during 2026, not receiving a K-1. The instructions say to report your share 'even if not received,' which refers to cash distributions. A missing K-1 means the request is still open, not that the rule does not apply.
  - Clients often don't recognize some interests as partnerships: publicly traded partnerships and some commodity ETFs in brokerage accounts issue K-1s, and multi-member LLCs file as partnerships. k1_count should count each entity separately.
  - Some interests do not produce a K-1: single-member LLCs that are disregarded entities, and grantor or revocable trusts, which usually send a grantor letter or report on the owner's own forms. The intake wording should not promise a K-1 for these.
- **Suggested document wording (not applied):** Schedule K-1 from each partnership, S corporation, estate, or trust in which the client held an interest or was a beneficiary at any time during 2026 (one per entity, count from k1_count), plus any Schedule K-3 a partnership or S corporation sends
- **Aliases:** K-1, Schedule K-1, Form K-1, Schedule K-3
- **Your call:** 

## R021

- **When:** `rental_property = true`
- **Document:** Rental income and expense records
- **Client reason:** You rented out a home, a room, or other real estate, so we need your rental income and expense records.
- **Source:** [2026 Instructions for Schedule E (Form 1040), Supplemental Income and Loss (draft, September 2026)](https://www.irs.gov/pub/irs-dft/i1040se--dft.pdf) (draft_instructions)
- **Excerpt:** "You must keep records to support items reported on Schedule E in case the IRS has questions about them."
- **Skeptic:** researcher's source refuted (Topic no. 414, Rental income and expenses); replaced with the skeptic's checked source. Refuted as primary source 2026-10-30. Topic 414 (guidance, HTTP 200) supports reporting rental income and expenses on Schedule E but has no record-keeping language and still cites 2018 through 2025. Use the 2026 draft Schedule E instructions (draft_instructions, HTTP 200, posted 09/24/2026). Suggested excerpt: 'You must keep records to support items reported on Schedule E in case the IRS has questions about them.' Keep Topic 414 as an alternate.
- **2026 notes:** Topic 414 is general guidance (last reviewed 24-Sep-2026). The 2026 Schedule E instructions exist only as a draft (https://www.irs.gov/pub/irs-dft/i1040se--dft.pdf, posted 09/24/2026), which still says 'You must keep records to support items reported on Schedule E.' 2026 changes in that draft: (1) the standard mileage rate for rental driving is 72.5 cents a mile from January 1 through June 30, 2026, and 76 cents a mile from July 1 through December 31, 2026, so mileage logs need dates; (2) the information reporting threshold rose from $600 to $2,000 for certain payments made on or after January 1, 2026, and Line A says Form 1099-MISC is generally required if you paid at least $2,000 in [...]
- **Worth a look:**
  - Topic 414 supports reporting rental income and expenses on Schedule E, but it does not explicitly say to keep records. If the reviewer wants record-keeping language, the Schedule E instructions say 'You must keep records to support items reported on Schedule E' (2026 draft PDF, and the 2025 HTML at https://www.irs.gov/instructions/i1040se).
  - Topic 414 still says expenses are deductible under certain conditions 'for tax years 2018 through 2025.' That reads as dated for 2026. Check that the page has not been revised or superseded before relying on it for expense rules.
  - The trigger should be renting out any real property during 2026, not receiving a 1099. That includes short-term or vacation rentals through platforms, renting a room or part of the home, and renting to relatives. Topic 414 says the cash or fair market value of property or services received for use of the property is rental income. With the [...]
  - Edge cases that change the schedule or remove the reporting: Publication 527 says rent from a dwelling used as a home and rented less than 15 days is not included in income. Substantial services for the tenant's convenience go on Schedule C (Topic 414). Personal property rentals do not go on Schedule E. Farm rental income may go on Form 4835. The [...]
- **Suggested document wording (not applied):** For each rental real estate property: rents and other amounts received (including the fair market value of any property or services received as rent), expenses paid, the number of days rented at a fair rental price and the number of days of personal use, purchase, improvement, and prior depreciation records, a dated mileage log if car expenses are claimed, and any Forms 1099-MISC or 1099-K received
- **Aliases:** rental income records, rental expense records, rental property records, Schedule E records, rental records, records of rental income, rental property income, rental property expenses
- **Your call:** 

## R022

- **When:** `estimated_payments_made = true`
- **Document:** Dates and amounts paid
- **Client reason:** You made estimated tax payments, so we need the date and amount of each payment.
- **Source:** [Form 1040-ES (2026), Estimated Tax for Individuals (Record of Estimated Tax Payments)](https://www.irs.gov/pub/irs-prior/f1040es--2026.pdf) (final_instructions)
- **Excerpt:** "Record of Estimated Tax Payments, Keep for Your Records, with columns for date paid and amount paid."
- **Skeptic:** researcher's source refuted (About Form 1040-ES, Estimated Tax for Individuals); replaced with the skeptic's checked source. Refuted as primary source 2026-10-30. The About Form 1040-ES page (final form page, HTTP 200) supports the activity but not the dates and amounts document, and it will switch to the 2027 form in early 2027. Use https://www.irs.gov/pub/irs-prior/f1040es--2026.pdf (final_instructions, HTTP 200, identical to the current 2026 form). Its 'Record of Estimated Tax Payments' has columns for date paid, amount paid, and 2025 overpayment credit applied.
- **2026 notes:** The About Form 1040-ES page (last reviewed 17-Sep-2026) links the 2026 Form 1040-ES as its current revision. It lists corrections to the 2026 form on 23-Feb-2026 (mailing addresses) and 25-Feb-2026 (state and local income tax deduction amount). The 2026 Form 1040-ES PDF (https://www.irs.gov/pub/irs-pdf/f1040es.pdf) has a 'Record of Estimated Tax Payments,' marked 'Keep for Your Records,' with columns for date paid, amount paid, and 2025 overpayment credit applied. Its due dates are 4/15/2026, 6/15/2026, 9/15/2026, and 1/15/2027, and it says the January payment is not required if the 2026 return is filed by February 1, 2027 and the full balance is paid. Publication 505 (2026) at [...]
- **Worth a look:**
  - The About page excerpt supports the activity and the form, not the 'dates and amounts' part. That support is in the 2026 Form 1040-ES PDF's Record of Estimated Tax Payments table, which WebFetch could not read (compressed fonts); I confirmed it by decoding the downloaded PDF. If a reviewer wants direct support for the document itself, the PDF may [...]
  - In early 2027 the About page and f1040es.pdf will switch to the 2027 Form 1040-ES. The 2026 version will move to a prior-year URL that does not exist yet, so I have not provided one.
  - The trigger should be any 2026 estimated payment, by any method: Direct Pay, EFTPS, Online Account, card, check with voucher, or a 2025 overpayment applied to 2026. The IRS sends no year-end form listing these payments, so the client's records or Online Account are the only sources. Watch for timing errors: the payment made in January 2026 for the [...]
  - Line 26 only needs the total. Payment dates matter for the underpayment penalty on Form 2210: Publication 505 says you may be charged a penalty if you don't pay enough by each due date. Keep the dates in the request even though the return line shows only the total.
- **Suggested document wording (not applied):** Date and amount of each federal estimated tax payment for tax year 2026 (including the payment made in January 2027, any 2025 overpayment applied to 2026, and any estimated tax credited by a trust), and the name and SSN each payment was made under, for example from the Record of Estimated Tax Payments, Direct Pay or EFTPS confirmations, or IRS Online Account payment history
- **Aliases:** estimated tax payments, estimated payment records, 1040-ES, Form 1040-ES, estimated payment dates and amounts, Record of Estimated Tax Payments, estimated payments, estimated tax payment records, estimated tax, estimated taxes
- **Your call:** 

## R023

- **When:** `gambling_winnings = true`
- **Document:** Form W-2G if received
- **Client reason:** You had gambling winnings, so we need any winnings statement the payer gave you.
- **Source:** [Topic no. 419, Gambling income and losses](https://www.irs.gov/taxtopics/tc419) (guidance)
- **Excerpt:** "A payer is required to issue you a Form W-2G, Certain Gambling Winnings"
- **Skeptic:** supported. Verified 2026-10-30: Topic 419 (irs.gov, last reviewed 24-Sep-2026) loads and supports the W-2G if received, reporting of all winnings, and the client's own win/loss records. 'guidance' label is correct. The 2026 changes ($2,000 threshold, 90% loss cap) appear in the final W-2G instructions (01/2026) and Pub 505 (2026), not in Topic 419.
- **2026 notes:** (1) Reporting threshold: the Instructions for Forms W-2G and 5754 (01/2026) say the minimum threshold for payments made in calendar year 2026 is $2,000 and will be adjusted yearly for inflation after that (https://www.irs.gov/instructions/iw2g). Proposed regulations REG-113229-25 in IRB 2026-19 state that the bingo, keno, and slot machine reporting threshold is $2,000 for calendar year 2026, for payments made on or after January 1, 2026, and that the statutory threshold applies even if the regulations are not finalized (https://www.irs.gov/irb/2026-19_IRB). (2) Loss limit: Publication 505 (2026) says that beginning in 2026 the Schedule A gambling loss deduction is limited to the lesser of [...]
- **Worth a look:**
  - Trigger wording: the rule should fire on any gambling activity with winnings, not only when a W-2G arrived. Topic 419 says taxpayers must report all gambling winnings, including winnings that are not reported on a Form W-2G. Keep the W-2G as 'if received' but always ask for the client's own records.
  - The higher 2026 threshold ($2,000 for slots, bingo, and keno, indexed after 2026) means many 2026 jackpots that would have produced a W-2G under the old $1,200 rule will not, so reliance on forms will undercount income.
  - The $2,000 bingo, keno, and slot figure appears in proposed regulations (REG-113229-25), which are not final; the W-2G instructions state the $2,000 figure generally for 2026 and point to Pub. 1099 and IRS.gov/InflationAdjustment for the per-type threshold, which I did not fetch. Other categories (poker tournaments, 300x wager rules, $5,000 [...]
  - Consider a separate rule for loss records (diary, casino win/loss statements, betting account history): losses are deductible only if the client itemizes, and starting in 2026 only up to 90% of losses, capped at winnings. Topic 419 does not yet reflect the 90% limit; Pub 505 (2026) and IRB 2026-19 do.
- **Suggested document wording (not applied):** Form W-2G, Certain Gambling Winnings, for each one received, plus the client's own record of gambling winnings and losses (diary, tickets, receipts, casino win/loss statements, or betting account history)
- **Aliases:** W-2G, Form W-2G, gambling winnings statement
- **Your call:** 

## R024

- **When:** `ip_pin_enrolled = true`
- **Document:** This year's IP PIN
- **Client reason:** You're in the IRS identity protection program, so we need this year's six-digit PIN.
- **Source:** [Frequently asked questions about the identity protection personal identification number (IP PIN)](https://www.irs.gov/identity-theft-fraud-scams/frequently-asked-questions-about-the-identity-protection-personal-identification-number-ip-pin) (guidance)
- **Excerpt:** "Each taxpayer who has an IP PIN must enter it on their tax return."
- **Skeptic:** supported. Verified 2026-10-30: the IP PIN FAQ (irs.gov, last reviewed 30-Oct-2026) loads and supports a PIN for each enrolled person, a new PIN each year, use on any return filed in that calendar year, and e-file rejection without it. 'guidance' label is correct. Plan for online retrieval from mid-January 2027 (get-an-IP-PIN page, IR-2026-127), not early January.
- **2026 notes:** (1) New PIN each year: the FAQ says a new IP PIN will be generated each year and must be used on any return filed during the current calendar year; CP01A notices go out from mid-December through early January, and online opt-ins must retrieve the PIN online beginning in early January (https://www.irs.gov/identity-theft-fraud-scams/frequently-asked-questions-about-the-identity-protection-personal-identification-number-ip-pin). (2) Online retrieval: the PIN is shown on the Profile page of the IRS online account, and the IRS app can also retrieve it; minors' PINs cannot be retrieved online and must be reissued by mail (within 21 days) after a call to 800-908-4490 [...]
- **Worth a look:**
  - 'This year's IP PIN' must mean the PIN for the filing year: a tax year 2026 return filed in 2027 needs the PIN issued for calendar 2027, not the 2026 PIN. The intake agent should not accept a PIN collected before January 2027.
  - The PIN is not available during the end-of-year maintenance window (the lost-PIN page says November through mid-January; IR-2026-127 says mid-November to mid-January; the FAQ says retrieval begins in early January). Reviewer should reconcile the 'early January' versus 'mid-January' wording and set intake reminders accordingly.
  - Each enrolled person on the return (taxpayer, spouse, each dependent) needs their own PIN; the FAQ says an e-filed return is rejected if a dependent's IP PIN is missing. Minors' PINs cannot be retrieved online.
  - Trigger: enrollment can happen by IRS assignment after identity theft (CP01A mailed) or by voluntary online opt-in (no letter). Ask both ways. One-time enrollees (per the get-an-IP-PIN page) are opted out at year end, so their status may differ by year.
- **Suggested document wording (not applied):** Identity Protection PIN (IP PIN) issued for calendar 2027 (the filing year for a tax year 2026 return) for each enrolled person on the return (taxpayer, spouse, dependents), from the CP01A notice or the Profile page of the IRS online account (online opt-ins get no notice; minors' PINs must be reissued by mail)
- **Aliases:** IP PIN, Identity Protection PIN, CP01A
- **Your call:** 

## R025

- **When:** `charitable_giving = true`
- **Document:** Receipts and written acknowledgments
- **Client reason:** You gave to charity, so we need your receipts and the written acknowledgments from the charities.
- **Source:** [Topic no. 506, Charitable contributions](https://www.irs.gov/taxtopics/tc506) (guidance)
- **Excerpt:** "you must obtain and keep in your records a contemporaneous written acknowledgment from the qualified organization"
- **Skeptic:** supported. Verified 2026-10-30: Topic 506 (irs.gov, last reviewed 24-Sep-2026) loads and supports the substantiation tiers and the 2026 non-itemizer cash deduction ($1,000, $2,000 joint). 'guidance' label is correct. Revise client_reason: the acknowledgment must show the amount and any goods or services received, and donor-advised fund and supporting organization gifts do not count for non-itemizers.
- **2026 notes:** (1) Non-itemizer deduction: Topic 506 says beginning with tax year 2026, if you do not itemize, you may deduct up to $1,000 ($2,000 if filing jointly) of cash contributions to certain qualified organizations (https://www.irs.gov/taxtopics/tc506). The Working Families Tax Cuts page lists this as section 70424, a permanent deduction for certain charitable cash contributions by non-itemizers (https://www.irs.gov/newsroom/one-big-beautiful-bill-provisions). (2) Publication 6196 (10-2026), Charitable Contribution Deduction Tips for Non-Itemizers, says the deduction is new for 2026 returns filed in 2027, requires cash gifts made during 2026 to eligible organizations, says donations to [...]
- **Worth a look:**
  - Trigger wording: for tax year 2026 the rule should fire for every client with charitable gifts, not only itemizers, because of the new non-itemizer cash deduction under Public Law 119-21 section 70424. Earlier logic that skipped charity questions for standard-deduction filers is now wrong.
  - For non-itemizers only cash gifts count (Pub 6196); noncash gifts (clothing, goods, stock) and gifts to donor-advised funds generally do not qualify. Intake should ask the type of gift and recipient, not just the amount.
  - Itemizers face a new 0.5% of AGI floor and a top-bracket reduction starting in 2026 (Pub 505 (2026)); Topic 506 does not yet mention the floor or the donor-advised fund exclusion.
  - Substantiation tiers to confirm: a bank record or written communication for any cash gift; a contemporaneous written acknowledgment for a single gift of $250 or more; Form 8283 if noncash deductions exceed $500; a qualified appraisal for items or groups over $5,000; reduce the deduction by the value of any benefit received (quid pro quo). Topic [...]
- **Suggested document wording (not applied):** Charitable gift records: for each cash gift, a bank or card record, canceled check, or receipt or letter from the charity showing its name, the date, and the amount; for any single gift of $250 or more, a written acknowledgment from the charity stating the amount and whether goods or services were given in return; for noncash gifts, records supporting Form 8283 when over $500 and a qualified appraisal for any item or group over $5,000
- **Aliases:** charitable receipts, donation receipts, charitable contribution receipts, written acknowledgment, contemporaneous written acknowledgment, Form 8283, charitable contributions, charitable donations, receipts and acknowledgments
- **Your call:** 

## R026

- **When:** `tips_income = true`
- **Document:** Tip records and how the employer reported them
- **Client reason:** You earned tips, so we need your tip records and how your employer reported them.
- **Source:** [Treasury, IRS issue final regulations listing occupations where workers customarily and regularly receive tips under the One, Big, Beautiful Bill (IR-2026-49, April 10, 2026)](https://www.irs.gov/newsroom/treasury-irs-issue-final-regulations-listing-occupations-where-workers-customarily-and-regularly-receive-tips-under-the-one-big-beautiful-bill) (guidance)
- **Excerpt:** "workers can take the deduction only for qualified tips that are included on Form W-2"
- **Skeptic:** supported. guidance: The IRS tip recordkeeping page (last reviewed 31-Jul-2026) covers daily tip records, W-2 box 12 code TP, box 14b, and Form 4137. IR-2026-49 supports only the list of qualifying forms. The final 2026 W-2/W-3 instructions and the draft 2026 Schedule 1-A confirm the form sources. Final 2026 Schedule 1-A instructions are not released yet.
- **2026 notes:** Final 2026 General Instructions for Forms W-2 and W-3 (https://www.irs.gov/instructions/iw2w3): new box 12 code TP reports the total cash tips reported to the employer; new box 14b holds up to two Treasury Tipped Occupation Codes, and "000" must be entered if any tips were received in a nonqualifying occupation. Draft 2026 Schedule 1-A (https://www.irs.gov/pub/irs-dft/f1040s1a--dft.pdf, created 6/16/26, listed on IRS.gov/DraftForms 09/04/2026): Part II employee tips are the larger of qualified tips in W-2 box 12 code TP or Form 4137 line 1 column (c); self-employed tips come only from Form 1099-NEC box 1(b), Form 1099-MISC box 13(a), or Form 1099-K box 1(c), limited to net profit less other [...]
- **Worth a look:**
  - Trigger wording: keying on the activity is right for income (all tips are taxable), but the deduction is limited to tips that appear on a W-2, a 1099, or Form 4137. On the 2026 draft Schedule 1-A, self-employed tips count only if shown in specific 1099 boxes, so a self-employed client whose tips were not on any 1099 may get no deduction. Confirm [...]
  - W-2 box 12 code TP is total cash tips reported to the employer, not necessarily qualified tips. Check box 14b for the occupation code and for "000" (a nonqualifying occupation), and check whether the employer is a specified service trade or business, which is excluded.
  - Secondary sources mentioned a box 12 code TS for tips in a specified service trade or business in a W-2 draft. The final 2026 W-2/W-3 instructions I read listed codes TA, TP, and TT and did not show TS; verify.
  - Form 4137 is for employees' tips not reported to the employer (and allocated tips); it does not cover self-employed tip income. Confirm how allocated tips in W-2 box 8 are treated for the deduction in the final 2026 instructions.
- **Suggested document wording (not applied):** Tip records and how they were reported: Form W-2 (box 12 code TP for cash tips reported to the employer, box 14b Treasury Tipped Occupation Code, box 7 social security tips, box 8 allocated tips); any Form 1099-NEC, 1099-MISC, or 1099-K showing qualified tips for self-employed work; the client's daily tip record (such as Form 4070-A) and copies of monthly tip reports given to the employer (such as Form 4070), used to figure any tips not reported [...]
- **Aliases:** tip records, tip income records, Form 4070, tips reported to employer, tips received, tip income
- **Your call:** 

## R027

- **When:** `overtime_pay = true`
- **Document:** Overtime records and how the employer reported them
- **Client reason:** You were paid overtime, so we need your overtime records and how your employer reported them.
- **Source:** [Updates to questions and answers about the new deduction for qualified overtime compensation (FS-2026-13, August 2026; announced in IR-2026-88)](https://www.irs.gov/pub/taxpros/fs-2026-13.pdf) (guidance)
- **Excerpt:** "Starting in tax year 2026, payors and employers are required to separately report qualified overtime compensation"
- **Skeptic:** supported. guidance: FS-2026-13 (Aug. 2026, supersedes FS-2026-01) Q6, Q20, Q22, and Q23 confirm the 2026 W-2 code TT reporting and that only amounts shown in code TT, or on a corrected W-2c, count. Final 2026 Schedule 1-A instructions are not released yet, and only the draft 2026 Schedule 1-A Part III exists.
- **2026 notes:** FS-2026-13 (https://www.irs.gov/pub/taxpros/fs-2026-13.pdf, August 2026) supersedes FS-2026-01. For 2026: employers must report qualified overtime on Form W-2 box 12 code TT (Q6, Q10); for tax years after 2025 employees may not deduct more than the code TT amount (Q20); if the amount is omitted or understated the employee must get a Form W-2c from the employer, and a substitute W-2 (Form 4852) cannot be used (Q22, Q23); the 2025 relief in Notice 2025-69 is not available after 2025; code TT shows the full premium amount even above the deduction cap (Q10); only the FLSA-required premium (the "half" in time and a half) counts; married taxpayers must file jointly and need SSNs valid for [...]
- **Worth a look:**
  - Trigger wording: overtime income is reportable either way, but for 2026 the deduction depends on the employer's form reporting (W-2 box 12 code TT). The agent should request documents on the activity but must not promise a deduction based on pay stubs alone, and should prompt the client to request a corrected W-2 if code TT is missing.
  - Only FLSA-required overtime premium qualifies. Exempt employees (executive, administrative, professional, outside sales, certain computer jobs, many transport workers) and overtime paid only under a union contract or state law above the FLSA requirement do not qualify, so the trigger "overtime pay" is broader than the deduction.
  - Source is a PDF fact sheet, not a stable HTML page; the only HTML FAQ page (FS-2026-01) is marked superseded. The FAQs are not published in the Internal Revenue Bulletin and may be updated.
  - 2026 Schedule 1-A and its instructions are draft or not yet released.
- **Suggested document wording (not applied):** Overtime records and how the employer reported them: Form W-2 with box 12 code TT (qualified overtime compensation, the FLSA-required premium above the regular rate); Form 1099-NEC box 1d or Form 1099-MISC box 14 in the rare case the client was an FLSA employee treated as a contractor; corrected Form W-2c if the code TT amount is missing or understated (a substitute Form 4852 does not count); pay stubs or payroll records to check the amount
- **Aliases:** overtime records, overtime pay records, overtime pay statement, overtime pay documentation, overtime compensation
- **Your call:** 

## R028

- **When:** `new_vehicle_loan = true`
- **Document:** Lender's interest statement
- **Client reason:** You paid interest on a loan for a new vehicle, so we need the lender's statement of interest paid.
- **Source:** [Instructions for Form 1098-VLI (Rev. December 2026), for calendar year 2026](https://www.irs.gov/instructions/i1098vli) (final_instructions)
- **Excerpt:** "If you are required to file Form 1098-VLI, you must provide a statement to the payer of record."
- **Skeptic:** supported. final_instructions: Instructions for Form 1098-VLI (Rev. 12/2026, calendar year 2026, updated 06-Oct-2026) are lender-facing. The taxpayer side is covered only by the draft 2026 Schedule 1-A Part IV and the T.D. 10054 final regulations (IRB 2026-39, effective Nov. 9, 2026). Final 2026 Schedule 1-A instructions are not released yet.
- **2026 notes:** Instructions for Form 1098-VLI (https://www.irs.gov/instructions/i1098vli, Rev. 12/2026, page last updated 06-Oct-2026) apply to calendar year 2026: a lender in a trade or business files when it receives $600 or more of interest on one specified passenger vehicle loan (the threshold applies per loan) and must give a statement to the payer of record (the principal borrower); boxes cover interest (box 1), year, make, model, and VIN (boxes 2a to 2d), origination and acquisition dates (3a, 3b), outstanding principal (4), refunds of overpaid interest (5), and checkboxes for original use (6) and US final assembly (7). About Form 1098-VLI page [...]
- **Worth a look:**
  - Trigger wording: "new vehicle" should be read as original use beginning with the taxpayer; the trigger should also require a loan originated after Dec. 31, 2024, secured by a first lien, for a personal-use car, minivan, van, SUV, pickup, or motorcycle under 14,000 pounds GVWR with US final assembly. Leases do not qualify.
  - Keep the trigger on the activity, not on receiving the form: a lender issues Form 1098-VLI only at $600 or more of interest per loan and only if it is in a trade or business, while the deduction is based on total qualifying interest paid. If no form arrives, request the lender's year-end or monthly loan statements.
  - The chosen source is lender-facing information return instructions; the taxpayer-side 2026 Schedule 1-A is draft only and its instructions were not found as of 2026-10-30.
  - The i1098vli page did not state the due date for the borrower statement; confirm (likely January 31, 2027) in the General Instructions for Certain Information Returns.
- **Suggested document wording (not applied):** Lender's vehicle loan interest statement (Form 1098-VLI), or the lender's year-end or monthly loan statements if no Form 1098-VLI arrives (lenders file only when in a trade or business and receiving $600 or more of interest on that loan); plus the vehicle identification number (VIN), the window sticker or other proof of United States final assembly, and the purchase and loan paperwork showing the loan was taken out after 2024 to buy the vehicle, [...]
- **Aliases:** vehicle loan interest statement, auto loan interest statement, car loan interest statement, lender interest statement, 1098-VLI, Form 1098-VLI, vehicle loan interest
- **Your call:** 

## R029

- **When:** `hsa_direct_contribution = true`
- **Document:** Contribution records, plus Form 5498-SA if received
- **Client reason:** You, or someone else for you, put money into your health savings account outside your paycheck, so we need your contribution records and any year-end contribution statement, which may not arrive until the end of May.
- **Source:** [Instructions for Forms 1099-SA and 5498-SA (12/2026)](https://www.irs.gov/instructions/i1099sa) (final_instructions)
- **Excerpt:** "You must provide a statement to the participant (generally Copy B) by May 31 of the subsequent year."
- **Skeptic:** researcher's source refuted (Publication 969 (2025), Health Savings Accounts and Other Tax-Favored Health Plans); replaced with the skeptic's checked source. Refuted as the primary source: Pub. 969 (2025) shows that Form 5498-SA reports contributions, but it is the 2025 edition and says nothing about timing. Use the final Instructions for Forms 1099-SA and 5498-SA (Revised 12/2026, final_instructions), which set the May 31, 2027 furnishing date. Keep Pub. 969 and the draft 2026 Form 8889 instructions (Jul 21, 2026) as alternates.
- **2026 notes:** Form timing is confirmed: the final Instructions for Forms 1099-SA and 5498-SA (12/2026, https://www.irs.gov/instructions/i1099sa) say the trustee "must provide a statement to the participant (generally Copy B) by May 31 of the subsequent year," so the 2026 Form 5498-SA is due to clients by May 31, 2027, after the April 15, 2027 filing deadline. The same page says to use the December 2026 revision of Form 5498-SA (address fields now split into separate boxes) for 2026 information. The draft 2026 Instructions for Form 8889 (Jul 21, 2026, https://www.irs.gov/pub/irs-dft/i8889--dft.pdf) say 2026 contributions can be made through April 15, 2027. They also say payroll contributions through a [...]
- **Worth a look:**
  - The source is a year-specific publication for 2025 returns, labeled guidance because no final 2026 Pub. 969 or final 2026 Form 8889 instructions exist yet. Recheck against the final 2026 editions when they post.
  - Trigger wording is right to say outside payroll. The Form 8889 instructions treat cafeteria plan payroll deductions as employer contributions, reported on W-2 box 12 code W, not line 2. But anyone with HSA contributions of any kind must file Form 8889, so a companion rule should make sure payroll-only HSA clients still get Form 8889 treatment from [...]
  - "Form 5498-SA if received" is the correct wording. The form is due May 31, 2027, so most clients won't have it at filing. The primary document must be the client's own records: bank or custodian deposit confirmations and statements.
  - Contributions made from January 1 through April 15, 2027 can count for 2026 or 2027 depending on the designation the client gave the trustee. The intake should ask for that designation, because the 5498-SA instructions say the trustee relies on the participant's designation.
- **Suggested document wording (not applied):** Records of each HSA deposit made outside payroll for 2026, by you or anyone else for you, including deposits made through April 15, 2027 (date, amount, and the tax year designated); Form 5498-SA only if already received
- **Aliases:** 5498-SA, Form 5498-SA, HSA contribution records, HSA contribution statement
- **Your call:** 

## R030

- **When:** `sold_home = true`
- **Document:** Closing statement, plus Form 1099-S if received
- **Client reason:** You sold a home, so we need the closing statement and any sale statement you received.
- **Source:** [Publication 523 (2025), Selling Your Home](https://www.irs.gov/publications/p523) (guidance)
- **Excerpt:** "If you didn't receive Form 1099-S, refer to your real estate transaction documents for the total amount you received for your home."
- **Skeptic:** researcher's source refuted (Topic no. 701, Sale of your home); replaced with the skeptic's checked source. Replace Topic 701 with Pub 523 (2025), labeled guidance, which supports the sale closing statement (real estate transaction documents), Form 1099-S, and purchase settlement statement for basis; Topic 701 supports only the 1099-S reporting trigger. No 2026 Pub 523 is posted, and Pub 523 (2025) box references (2, 4, 6) do not match the 12/2026 Form 1099-S layout. Recheck when the 2026 Pub 523 is posted.
- **2026 notes:** Topic 701 (last reviewed 12-Oct-2026) still shows the $250,000 ($500,000 joint) exclusion and 2 of 5 years ownership and use tests; it relies on Pub 523 (2025), and no 2026 Pub 523 was found. Instructions for Form 1099-S (Rev. 12/2026, https://www.irs.gov/instructions/i1099s) are final for 2026 information filed in early 2027 and say that beginning in tax year 2026 'digital assets will be reported on Form 1099-S when used in the sale or exchange of real estate.' The 2026 form renumbers boxes: Box 2a Total Gross Proceeds, 2b Total Cash Gross Proceeds, 2c Digital Asset Gross Proceeds, Box 4 Buyer's Part of Real Estate Tax, Box 5 Reserved, Box 6 noncash consideration checkbox, Box 7 foreign [...]
- **Worth a look:**
  - Trigger wording should stay on the sale, not on Form 1099-S. Per the 1099-S instructions, a closing agent may skip the form for a principal residence sold for $250,000 or less ($500,000 if the seller certifies being married) when the seller signs a certification that the full gain is excludable, so many sellers get no 1099-S.
  - Topic 701 says any informational income-reporting document 'such as Form 1099-S' triggers reporting, so the agent should also ask about similar statements, not only a 1099-S.
  - If no 1099-S was received and the whole gain is excludable, the sale may not need to be reported at all, but the preparer still needs the closing statement (and purchase records) to confirm that.
  - Basis requires the original purchase closing statement and improvement records (Pub 523 Worksheet 2 line 4b says the settlement statement should list the buying fees); consider whether that belongs in this rule or in a separate basis rule.
- **Suggested document wording (not applied):** Closing statement from the sale (settlement statement or Closing Disclosure), plus Form 1099-S (Proceeds From Real Estate Transactions) if received; also the closing statement from the original purchase and records of improvements, to figure basis
- **Aliases:** 1099-S, Form 1099-S, closing statement, sale closing statement, settlement statement
- **Your call:** 

## T001

- **When:** `clean_energy_installed = true` and `clean_energy_install_year = 2026`
- **Document:** None (trap: the credit ended, so no federal document is needed)
- **Source:** [IRS FS-2025-05, FAQs for modification of sections 25C, 25D, 25E, 30C, 30D, 45L, 45W, and 179D under Public Law 119-21](https://www.irs.gov/newsroom/faqs-for-modification-of-sections-25c-25d-25e-30c-30d-45l-45w-and-179d-under-public-law-119-21-139-stat-72-july-4-2025-commonly-known-as-the-one-big-beautiful-bill-obbb) (guidance)
- **Excerpt:** "An expenditure with respect to an item is treated as made when the original installation of the item is completed."
- **Skeptic:** supported. Supported by IRS FS-2025-05 (guidance, FAQ 7 sets the installation-completed timing). Cite the canonical URL ending '-bill-obbb'; the PRD's '-bill-act-obbb' URL 301-redirects there. The 2026 draft Form 5695 instructions agree. Final 2026 instructions are not posted as of 2026-10-30. Keep a separate carryforward rule so this trap does not block a legitimate 2026 Form 5695.
- **2026 notes:** FAQ 7 of FS-2025-05 says an expenditure is treated as made when original installation is completed, so a system paid for in 2025 but installed in 2026 gets no credit; for new construction, the expenditure is made when the taxpayer's original use of the structure begins (https://www.irs.gov/newsroom/faqs-for-modification-of-sections-25c-25d-25e-30c-30d-45l-45w-and-179d-under-public-law-119-21-139-stat-72-july-4-2025-commonly-known-as-the-one-big-beautiful-bill-obbb). The 2026 DRAFT Instructions for Form 5695 (dated Aug 27, 2026, posted 09/01/2026) retitle the form 'Carryforward of Residential Energy Credit' and limit it to taking any unused residential clean energy credit carried forward [...]
- **Worth a look:**
  - The trap is right for a 2026 installation, but a client with an unused residential clean energy credit carryforward from 2025 (or earlier) still files 2026 Form 5695 to use it. Intake should ask whether the prior-year Form 5695 showed a carryforward (line 16) and request the prior-year return; this trap must not suppress that request.
  - Trigger wording should key on the date installation was completed (or, for new construction, the date the client began using the home), not on the payment or contract date. A system paid for in 2025 but finished in 2026 gets nothing; a system finished in 2025 belongs on the 2025 return (or an amended 2025 return), not the 2026 return.
  - The IRS Residential Clean Energy Credit page (last reviewed 04-Jul-2026) uses different wording ('placed in service after December 31, 2025') and still carries stale text about claiming the credit 'until the credit begins to phase out in 2033'. Rely on FS-2025-05 FAQ 7 and the 2026 draft Form 5695 instructions.
  - 'Nothing federal' is about the credit only. The residential clean energy page notes that some state energy incentives labeled as rebates could be included in gross income, so a reviewer should confirm whether a separate rule asks about state or utility incentive payments received in 2026.
- **Aliases:** Form 5695, 5695, solar receipt, solar invoice, clean energy invoice, residential clean energy credit, solar panel, solar installation, solar energy system
- **Your call:** 

## T002

- **When:** `efficiency_improvements = true` and `efficiency_improvements_year = 2026`
- **Document:** None (trap: the credit ended, so no federal document is needed)
- **Source:** [IRS FS-2025-05, FAQs for modification of sections 25C, 25D, 25E, 30C, 30D, 45L, 45W, and 179D under Public Law 119-21](https://www.irs.gov/newsroom/faqs-for-modification-of-sections-25c-25d-25e-30c-30d-45l-45w-and-179d-under-public-law-119-21-139-stat-72-july-4-2025-commonly-known-as-the-one-big-beautiful-bill-obbb) (guidance)
- **Excerpt:** "The credit will not be allowed for any property placed in service after December 31, 2025."
- **Skeptic:** supported. Supported by IRS FS-2025-05 (guidance): 25C is not allowed for property placed in service after December 31, 2025. Cite the canonical URL ending '-bill-obbb'. The 2026 draft Form 5695 instructions add the word 'expenditures', which also covers a 2026 home energy audit. Do not cite the IRS 25C credit page, which says 'before December 31, 2025'. Final 2026 instructions are not posted as of 2026-10-30.
- **2026 notes:** The 2026 DRAFT Instructions for Form 5695 (dated Aug 27, 2026) state under What's New that energy efficient home improvement credits cannot be claimed for expenditures or property placed in service after December 31, 2025, and the 2026 form is retitled 'Carryforward of Residential Energy Credit' with no 25C section (https://www.irs.gov/pub/irs-dft/i5695--dft.pdf). The 25C credit has no carryforward ('You can't apply any excess credit to future tax years', https://www.irs.gov/credits-deductions/energy-efficient-home-improvement-credit), so nothing from 25C reaches a 2026 return. FS-2025-05 FAQ 6 also ends manufacturers' periodic written reports for 25C property [...]
- **Worth a look:**
  - Trigger wording should key on the date the item was installed (placed in service), not the purchase or payment date. Items installed by December 31, 2025 belong on the 2025 return (or an amended 2025 return); items installed in 2026 get nothing.
  - The IRS Energy Efficient Home Improvement Credit page (last reviewed 28-Apr-2026) says property placed in service 'before December 31, 2025', while FS-2025-05 and the draft 2026 instructions say the credit ends for property placed in service 'after December 31, 2025'. This only matters for a December 31, 2025 install on a 2025 return, but a [...]
  - Do not let this trap suppress a separate request for a prior-year Form 5695 when the household also has an unused residential clean energy (25D) carryforward; the 2026 Form 5695 still exists for that.
  - 'Nothing federal' assumes a personal residence. Energy improvements to a rental property or business-use space can be capital or depreciation items on other schedules, and some state incentives labeled as rebates may be taxable income per the IRS 25C page. A reviewer should confirm other rules cover those.
- **Aliases:** Form 5695, 5695, energy efficiency receipts, home energy improvement receipts, manufacturer certification statement, energy efficient home improvement credit, energy-efficient, energy efficiency
- **Your call:** 

## T003

- **When:** `ev_purchased = true` and `ev_acquired_date >= "2026-01-01"` and `ev_acquired_date <= "2026-12-31"`
- **Document:** None (trap: the credit ended, so no federal document is needed)
- **Source:** [IRS FS-2025-05, FAQs for modification of sections 25C, 25D, 25E, 30C, 30D, 45L, 45W, and 179D under Public Law 119-21](https://www.irs.gov/newsroom/faqs-for-modification-of-sections-25c-25d-25e-30c-30d-45l-45w-and-179d-under-public-law-119-21-139-stat-72-july-4-2025-commonly-known-as-the-one-big-beautiful-bill-obbb) (guidance)
- **Excerpt:** "The credit will not be allowed for any vehicle acquired after September 30, 2025."
- **Skeptic:** supported. Supported by IRS FS-2025-05 (guidance): 30D and 25E are not allowed for vehicles acquired after September 30, 2025, and FAQ 2 defines acquired as a binding written contract plus a payment. Cite the canonical URL ending '-bill-obbb'. The 2026 draft Form 8936 instructions agree. Final 2026 instructions are not posted as of 2026-10-30. Intake must check the contract and payment date, not the delivery date.
- **2026 notes:** FS-2025-05 FAQ 2 defines acquired as the date a written binding contract is entered into and a payment (including a nominal down payment or trade-in) has been made; FAQ 3 says a vehicle acquired by September 30, 2025 still qualifies when placed in service (possession) later, and the buyer should receive a time of sale report from the dealer (https://www.irs.gov/newsroom/faqs-for-modification-of-sections-25c-25d-25e-30c-30d-45l-45w-and-179d-under-public-law-119-21-139-stat-72-july-4-2025-commonly-known-as-the-one-big-beautiful-bill-obbb). The 2026 DRAFT Instructions for Form 8936 (dated Aug 10, 2026, posted 09/25/2026) keep Form 8936 and Schedule A for vehicles acquired on or before [...]
- **Worth a look:**
  - Trigger wording must use the acquisition date (signed binding contract plus a payment), not the delivery or purchase-completion date. A client who 'bought' an EV in 2026 but signed and paid a deposit on or before September 30, 2025 still qualifies and needs the dealer's time of sale report and Form 8936 with Schedule A on the 2026 return. Intake [...]
  - If a dealer applied a point-of-sale credit transfer at a 2026 delivery, Form 8936 and Schedule A are mandatory, and the draft 2026 instructions say the client must repay the transferred amount if they no longer qualify. Request the seller report in that case rather than treating it as nothing.
  - This trap must not suppress separate 2026 rules that an EV purchase can trigger: the car loan interest deduction (new vehicle, final assembly in the US, personal use, loan after 2024, up to $10,000) and the home charger credit (30C) for chargers placed in service by June 30, 2026. Both were confirmed on IRS pages fetched in this session.
  - A leased EV never gives the lessee the credit (the draft instructions say only the lessor is entitled), so the trap also holds for leases; business-use EVs fall under 45W, which ended on the same acquisition date, but depreciation for business use is a separate item.
- **Aliases:** Form 8936, 8936, clean vehicle credit, EV purchase agreement, clean vehicle seller report, time of sale report
- **Your call:** 

