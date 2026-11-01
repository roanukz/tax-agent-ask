# Simulator fidelity check

Run `fidelity-20261101T041142Z`, model `claude-haiku-5-5`. 28 train and dev households, 11 scripted questions each. All households are synthetic. Not tax advice.

## Result

- Revealed facts compared with the truth: 1004
- Matching the truth: 1000 (99.6%)
- Matching the truth in the 20 households drawn by complexity (the PRD 6.4 sample): 703 of 705 (99.7%)
- Answers scored: 308 of 308. An answer that failed twice or had no revealed object can't be scored.
- Bar: at least 98% of compared facts match, and every answer is scored. **Met.**
- Match rate without the 11 households that have a recall gap: 100.0%
- Asked facts (with a non-null truth) that the answers revealed: 999 of 1065 (93.8%). Shown for judgment, not part of the bar: a recall gap or a low-jargon client can leave an asked fact out on purpose.
- q00, what changed since last year: remembered changes the answers named: 67 of 76 (88.2%). Recall gaps that broke on it: 0. Shown for judgment, not part of the bar.
- Facts that ended, which the client's change list shows as "Last year only" lines, in 9 households, read in the answers kept after the contradiction retry: revealed values that match the truth: 19 of 19 (100.0%). Revealed as true this year: 0. Revealed as true in a first reply the simulator sent back: 0. Other values that differ (null, or not a yes or no): 0. Asked about and revealed: 19 of 26. Control, the same facts in the 10 households where they are true this year: 32 of 32 match (100.0%). These values count in the match rate too, but at 19 of 1004 compared the bar can't see a misread on its own, so read the Facts that ended table; a misread the simulator sent back shows only in the Contradictions sent back table.
- Mismatches: 4. Anomalies: 0. Leaks code caught: 6 (form_number_in_text 6).
- Answers that needed a retry for an unusable reply: 0. Answers that failed twice: 0.
- Answers the simulator sent back once, because they contradicted a fact outside the recall gap: 2 of 308 (0.6%). Facts the retry corrected: 2. Facts the answers kept still get wrong, counted as mismatches: 0. Answers whose retry changed a fact the recall gap hides, so the first answer was kept: 0. The match rate is on the answers kept, so this is the simulator's raw error; the Contradictions sent back table lists each fact.
- Tokens: input 230,483, output 40,908, cache read 240,915, cache write 23,913. Cost at list price: $0.0489 (reserved before the first call: $0.2685).

A recall gap can make the client give a wrong value on purpose, then correct it when asked about the right topic; those mismatches show the gap and which later answer, if any, corrected them. A correction on a question that doesn't ask about the fact is also listed under leaks as `changed_off_topic`.

## Households

Chosen deterministically from train and dev: 20 drawn, stratified by complexity, plus every household whose client prompt has a "Last year only" line (H011, H015, H022, H032, H059, H065, H067, H085, H091), which adds 8. By complexity: complex 10, medium 12, simple 6.

H010, H011, H013, H015, H018, H021, H022, H032, H036, H042, H043, H046, H048, H051, H054, H058, H059, H065, H067, H080, H081, H082, H085, H087, H089, H091, H093, H095

## Questions

| ID | Topic | Compared | Matched | Facts asked |
|---|---|---|---|---|
| q00 | what changed | 72 | 72 | each change the client remembers |
| q01 | jobs | 112 | 108 | w2_employer_count, tips_income, overtime_pay, unemployment |
| q02 | side work | 49 | 49 | self_employment, business_app_payments |
| q03 | banks and investments | 134 | 134 | interest_income, dividend_income, sold_investments, digital_asset_transactions, k1_count |
| q04 | retirement and Social Security | 56 | 56 | retirement_distribution, social_security |
| q05 | health coverage and HSAs | 84 | 84 | marketplace_coverage, hsa_withdrawal, hsa_direct_contribution |
| q06 | home | 111 | 111 | has_mortgage, bought_home, sold_home, rental_property |
| q07 | school and child care | 84 | 84 | tuition_paid, student_loan_interest, dependent_care_paid |
| q08 | charity and gambling | 56 | 56 | charitable_giving, gambling_winnings |
| q09 | energy upgrades and vehicles | 121 | 121 | clean_energy_installed, clean_energy_install_year, efficiency_improvements, efficiency_improvements_year, ev_purchased, ev_acquired_date, new_vehicle_loan |
| q10 | anything else that changed | 125 | 125 | filing_status, dependents_count, new_dependent, estimated_payments_made, state_refund_received, ip_pin_enrolled |

Fact keys no question asks about: itemized_last_year.

- **q00:** Before we go topic by topic, has anything changed for you or your household since last year?
- **q01:** Let's start with work. How many employers paid you wages this year, counting any job you left? Did you get tips, overtime pay, or unemployment benefits?
- **q02:** Did you do any freelance, contract, or gig work, or run a business of your own? If you did, did customers pay you through a payment app or an online marketplace?
- **q03:** Did you earn interest from a bank or credit union, get dividends, or sell any stocks, funds, or cryptocurrency? Do you own a share of a partnership or small company, or get income from an estate or trust?
- **q04:** Did you take any money out of a retirement account or pension, or roll one over? Did you receive Social Security benefits?
- **q05:** How did your household get health insurance this year? Did anyone buy a plan through HealthCare.gov or a state exchange, and did you put money into or take money out of a health savings account?
- **q06:** Tell me about your home. Do you have a mortgage, did you buy or sell a home this year, and do you rent out any property?
- **q07:** Did you pay college tuition or student loan interest for yourself or anyone you support? Did you pay for child care, or care for another dependent, so you could work?
- **q08:** Did you give money or goods to charity this year? Did you win any money gambling, at a casino, or in a lottery?
- **q09:** Did you put in solar panels or other clean energy equipment, or make energy-saving upgrades like new windows or a heat pump? If so, what year was the work finished? Did you buy an electric car, or take out a loan for any new car, and if so, when did you sign the purchase contract?
- **q10:** Did anything else change for you this year that we haven't talked about, such as getting married or divorced, a new baby or someone new you support, tax payments you sent in yourself, a state tax refund, or a special PIN from the IRS?

## What changed since last year

q00 asks what changed before any topic comes up, as the agent does. It asks about no recall gap's topic, so the client should name every change it remembers and none its recall gap hides. A change left out is how a false "nothing changed" shows; a gap that broke means the client handed over the fact the agent is meant to probe for.

Remembered changes the answer left out:

| Household | Fact | Truth |
|---|---|---|
| H011 | tuition_paid | false |
| H022 | dependents_count | 1 |
| H022 | self_employment | false |
| H022 | business_app_payments | false |
| H022 | tuition_paid | false |
| H032 | marketplace_coverage | false |
| H032 | tuition_paid | false |
| H059 | self_employment | false |
| H093 | dependents_count | 3 |

Recall gaps that broke (the hidden fact revealed at its true value):

None.

## Facts that ended

Each row is an answer that asks about or reveals a fact its household's change list shows as "Last year only": true last year, false this year. A client that reads such a line as this year's reveals the fact as true, and in an agent run on that household the misread counts against the agent, as a request for a document the client won't get. Only the words show whether an answer is about last year, so read every row. Each row is the answer kept, after the contradiction retry: a first reply that revealed such a fact as true was sent back, and shows in the Contradictions sent back table.

| Household | Question | Ended facts | Revealed | Answer |
|---|---|---|---|---|
| H011 | q00 | tuition_paid | tuition_paid not revealed | Yes, a few things. A new dependent joined our household, so we now have two dependents instead of one. I also started receiving Social Security benefits, and we bought a home and took on a mortgage. We also made energy-saving upgrades to the house this year. |
| H011 | q07 | tuition_paid | tuition_paid false | No, we didn't pay any college tuition or student loan interest this year. We also didn't pay for child care or dependent care so we could work. |
| H015 | q00 | self_employment | self_employment false | Yes, I stopped doing my freelance side work, so I don't have that income this year. Nothing else major has changed that I can think of. |
| H015 | q02 | self_employment | self_employment false | No, I don't have any of that this year. The freelance work I did last year has stopped, so there are no app or marketplace payments either. |
| H022 | q00 | self_employment, business_app_payments, tuition_paid | self_employment not revealed; business_app_payments not revealed; tuition_paid not revealed | Yes, a few things! I have a new dependent, a baby who joined the household this year. I also took a withdrawal from my retirement account and had some gambling winnings, which I didn't have last year. |
| H022 | q02 | self_employment, business_app_payments | self_employment false; business_app_payments false | No, I didn't do any freelance or gig work or run a business this year. Nothing came through a payment app or marketplace either. |
| H022 | q07 | tuition_paid | tuition_paid false | No tuition or student loan interest this year, and no child care costs either. I'm not sure I'm following the phrasing about the college part, but nothing was paid there. |
| H032 | q00 | marketplace_coverage, tuition_paid | marketplace_coverage not revealed; tuition_paid not revealed | Yes, a few things. I went from two jobs with employers to just one, and I started freelance and small business work this year where customers pay me through an app. I also took money out of a retirement account for the first time, and I'm a bit worried about how that gets taxed. |
| H032 | q05 | marketplace_coverage | marketplace_coverage false | I didn't buy a plan through HealthCare.gov or a state exchange this year. I did take money out of my health savings account, but I didn't put any money in. |
| H032 | q07 | tuition_paid | tuition_paid false | No, I didn't pay any college tuition or student loan interest this year, and I didn't pay for child care or dependent care so I could work. |
| H059 | q00 | self_employment, business_app_payments | self_employment not revealed; business_app_payments false | Yes. We bought a home this year and took out a mortgage for it. I also stopped the side work I did last year where customers paid through an app. |
| H059 | q02 | self_employment, business_app_payments | self_employment false; business_app_payments false | No, I didn't do any freelance or gig work or run a business this year. |
| H065 | q00 | marketplace_coverage | marketplace_coverage false | Yes, a few things! We bought our first home this year, so we have a mortgage now. We also put in solar panels, did some energy-saving upgrades around the house, and I had some gambling winnings. We don't have that marketplace health plan anymore, though. |
| H065 | q05 | marketplace_coverage | marketplace_coverage false | We have insurance through my job, I think. I didn't buy anything through the marketplace this year, and I didn't touch a health savings account. |
| H067 | q00 | tuition_paid | tuition_paid false | Yes, a few things. I started some side work this year where customers pay me through an app, I bought and sold some cryptocurrency, and I did some energy-saving upgrades to the house. I'm no longer paying college tuition. |
| H067 | q07 | tuition_paid | tuition_paid false | No, no tuition or student loan interest this year, and no child care costs either. |
| H085 | q00 | marketplace_coverage | marketplace_coverage false | Yes. I took a retirement account withdrawal this year, which I didn't have last year. I also had a health plan through HealthCare.gov or a state exchange last year, but I don't have one this year. |
| H085 | q05 | marketplace_coverage | marketplace_coverage false | No one in my household bought a plan through HealthCare.gov or a state exchange this year. I did put money into a health savings account, but I didn't take any money out. |
| H091 | q00 | tuition_paid | tuition_paid false | Oh yes, quite a bit! I started doing some freelance and small business work this year, which is new for me. I also put in some energy-saving upgrades around the house in 2026. The tuition payments I made last year aren't happening anymore, though. |
| H091 | q07 | tuition_paid | tuition_paid false | No tuition this year and no student loan interest either. But yes, we paid for child care so we could both work, for our dependent. |

## Contradictions sent back

When an answer contradicts a fact outside the recall gap, the simulator tells its model, in its own conversation, which facts it got wrong, and asks the same question again, once. The answer it gives is kept even when it still contradicts, unless it reveals a fact the recall gap hides differently from the first answer, which would break the gap or undo it; then the first answer is kept. An agent would see only the answer kept. Each row is a fact the first answer contradicted or the answer kept still gets wrong; "not revealed" means that answer left the fact out. A corrected fact is not a mismatch, and an unresolved one is, as is one marked "unresolved, recall gap kept".

| Household | Question | Fact | First answer | Answer kept | Truth | Outcome |
|---|---|---|---|---|---|---|
| H048 | q03 | k1_count | 0 | 1 | 1 | corrected |
| H087 | q09 | new_vehicle_loan | true | false | false | corrected |

## Mismatches

| Household | Question | Fact | Revealed | Truth | Corrected later | Recall gap |
|---|---|---|---|---|---|---|
| H022 | q01 | w2_employer_count | 2 | 3 | no | forgets_new_part_time_job_unless_asked_about_other_work |
| H058 | q01 | w2_employer_count | 1 | 2 | no | forgets_new_part_time_job_unless_asked_about_other_work |
| H065 | q01 | w2_employer_count | 1 | 2 | no | forgets_new_part_time_job_unless_asked_about_other_work |
| H082 | q01 | w2_employer_count | 1 | 2 | no | forgets_second_job_unless_asked_about_other_work |

## Unscored answers

These fail the bar whatever the match rate, since nothing in them could be compared.

None.

## Anomalies

Revealed entries with an unknown key or a value that doesn't fit the fact's type.

None.

## Leaks code caught

The first three kinds are facts revealed outside the question, by a client whose persona doesn't volunteer. `unasked_fact`: no earlier answer revealed it, so the agent learns something it didn't ask for. `changed_off_topic`: an earlier answer gave another value; a recall gap breaking on its own topic looks like this and is fine, while one breaking on any other topic is a leak, so check the gap. `restated_fact`: the earlier value again, which tells the agent nothing new; the mildest kind. `fact_key_in_text` and `form_number_in_text`: words in the answer the agent would see.

| Household | Question | Kind | What | Detail |
|---|---|---|---|---|
| H022 | q03 | form_number_in_text | K-1 |  |
| H042 | q00 | form_number_in_text | W-2 |  |
| H046 | q01 | form_number_in_text | W-2 |  |
| H048 | q00 | form_number_in_text | W-2 |  |
| H091 | q01 | form_number_in_text | W-2 |  |
| H091 | q03 | form_number_in_text | K-1 |  |

## Simulator errors

None.

## Human review

Roanuk reads 30 answers in `data/labels/sim_answers_for_review.md` for leaks the revealed JSON doesn't show. Every leak he finds gets fixed before Block 5. He also reads the Facts that ended table, and the first replies in the Contradictions sent back table, and Block 4 is done only when every fact that ended and was revealed as true this year, in a first reply or in the answer kept, is fixed or accepted.
