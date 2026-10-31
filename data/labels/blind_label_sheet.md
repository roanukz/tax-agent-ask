# Blind label sheet

30 synthetic households (10 train, 20 dev, stratified by complexity) for checking the rules by hand. Each description comes from the household's true facts and what changed since last year. Nothing the rules produce appears here, on purpose.

How to label: for each household, write every item the preparer should ask the client for, for the 2026 federal return only, in the documents column of data/labels/blind_labels.csv, one row per household. Separate items with semicolons, and add x2 or x3 after an item when the household needs more than one of the same kind. Use whatever short name you would put on a request list. Leave out what every client brings, such as a photo ID or bank details, and write none if the preparer should ask for nothing. Put your name in labeled_by and anything worth noting in notes.

The label page does the same with a box per item and a count box: `uv run python -m generator.label_ui`.

When every household has a label, compare them with the rules, with the label page's Compare button or `uv run python -m generator.labels compare --reveal`. Either first copies the labels to data/labels/blind_labels_before_compare.csv, since labels written after seeing the rules' answers are no longer blind.

Not tax advice. All households are synthetic.

## H002 (dev, simple)

This year:

- Filing status: married, filing jointly.
- Dependents: none.
- Paid wages by 1 employer, counting any job that ended during the year.
- Took the standard deduction on last year's return.

Changed since last year:

- Nothing changed since last year.

## H011 (dev, complex)

This year:

- Filing status: head of household.
- Dependents: 2, one of them new this year (a birth, an adoption, or someone newly supported).
- Paid wages by 1 employer, counting any job that ended during the year.
- Earned interest from a bank, credit union, or bonds.
- Received dividends from stocks or mutual funds.
- Received Social Security benefits.
- Has a mortgage on their home.
- Bought a home this year.
- Owns a share of 2 partnerships, small companies, estates, or trusts.
- Made energy-saving home upgrades, such as windows, insulation, or a heat pump, put in during 2026.
- Took the standard deduction on last year's return.

Changed since last year:

- Dependents: 1 last year, 2 this year.
- New this year: a dependent who joined the household.
- New this year: Social Security benefits.
- New this year: a mortgage.
- New this year: a home purchase.
- Last year only: college tuition payments.
- New this year: energy-saving home upgrades.

## H012 (dev, simple)

This year:

- Filing status: head of household.
- Dependents: 1.
- Paid wages by 1 employer, counting any job that ended during the year.
- Took the standard deduction on last year's return.

Changed since last year:

- Nothing changed since last year.

## H013 (train, simple)

This year:

- Filing status: married, filing jointly.
- Dependents: none.
- Paid wages by 1 employer, counting any job that ended during the year.
- Took the standard deduction on last year's return.

Changed since last year:

- Nothing changed since last year.

## H015 (dev, medium)

This year:

- Filing status: married, filing jointly.
- Dependents: 1.
- Paid wages by 1 employer, counting any job that ended during the year.
- Received dividends from stocks or mutual funds.
- Got a state income tax refund this year.
- Has a mortgage on their home.
- Earned tips at work.
- Itemized deductions on last year's return.

Changed since last year:

- Last year only: freelance, gig, or small business work.

## H018 (train, complex)

This year:

- Filing status: head of household.
- Dependents: 3.
- Paid wages by 3 employers, counting any job that ended during the year.
- Earned interest from a bank, credit union, or bonds.
- Sold some stocks, bonds, or mutual funds.
- Got a state income tax refund this year.
- Bought health insurance through HealthCare.gov or a state exchange.
- Has a mortgage on their home.
- Paid for child care or other dependent care so they could work.
- Owns a share of 1 partnership, small company, estate, or trust.
- Gave money or goods to charity.
- Installed solar panels or similar clean energy equipment; the work was finished in 2026.
- Made energy-saving home upgrades, such as windows, insulation, or a heat pump, put in during 2026.
- Itemized deductions on last year's return.

Changed since last year:

- Employers paying wages: 4 last year, 3 this year.
- New this year: a health plan bought through HealthCare.gov or a state exchange.
- New this year: a clean energy installation.
- New this year: energy-saving home upgrades.

## H020 (dev, medium)

This year:

- Filing status: single.
- Dependents: none.
- Paid wages by 2 employers, counting any job that ended during the year.
- Sold or traded cryptocurrency.
- Took the standard deduction on last year's return.

Changed since last year:

- Employers paying wages: 1 last year, 2 this year.
- New this year: cryptocurrency sales or trades.

## H021 (dev, simple)

This year:

- Filing status: married, filing jointly.
- Dependents: 2.
- Paid wages by 1 employer, counting any job that ended during the year.
- Took the standard deduction on last year's return.

Changed since last year:

- Nothing changed since last year.

## H022 (train, complex)

This year:

- Filing status: single.
- Dependents: 1, one of them new this year (a birth, an adoption, or someone newly supported).
- Paid wages by 3 employers, counting any job that ended during the year.
- Earned interest from a bank, credit union, or bonds.
- Received dividends from stocks or mutual funds.
- Took money out of a retirement account.
- Owns a share of 1 partnership, small company, estate, or trust.
- Rented out a property.
- Won money gambling.
- Was paid overtime at work.
- Took the standard deduction on last year's return.

Changed since last year:

- Dependents: 0 last year, 1 this year.
- New this year: a dependent who joined the household.
- Employers paying wages: 2 last year, 3 this year.
- Last year only: freelance, gig, or small business work, with some customers paying through an app or online marketplace.
- New this year: a retirement account withdrawal.
- Last year only: college tuition payments.
- New this year: gambling winnings.

## H024 (dev, medium)

This year:

- Filing status: single.
- Dependents: 1.
- Paid wages by 2 employers, counting any job that ended during the year.
- Received unemployment benefits.
- Bought an electric vehicle; signed the contract and made a payment on 2026-02-17.
- Took the standard deduction on last year's return.

Changed since last year:

- Employers paying wages: 1 last year, 2 this year.
- New this year: unemployment benefits.
- New this year: an electric vehicle purchase.

## H029 (dev, medium)

This year:

- Filing status: head of household.
- Dependents: 1.
- Paid wages by 2 employers, counting any job that ended during the year.
- Spent or withdrew money from a health savings account.
- Sold a home this year.
- Took the standard deduction on last year's return.

Changed since last year:

- Employers paying wages: 1 last year, 2 this year.
- New this year: a home sale.

## H044 (train, simple)

This year:

- Filing status: single.
- Dependents: 1.
- Paid wages by 1 employer, counting any job that ended during the year.
- Took the standard deduction on last year's return.

Changed since last year:

- Nothing changed since last year.

## H046 (train, medium)

This year:

- Filing status: married, filing jointly.
- Dependents: none.
- Paid wages by 1 employer, counting any job that ended during the year.
- Did freelance or gig work, or ran a small business of their own.
- Earned interest from a bank, credit union, or bonds.
- Received unemployment benefits.
- Has a mortgage on their home.
- Bought a home this year.
- Earned tips at work.
- Took the standard deduction on last year's return.

Changed since last year:

- New this year: freelance, gig, or small business work.
- New this year: unemployment benefits.
- New this year: a mortgage.
- New this year: a home purchase.

## H050 (dev, complex)

This year:

- Filing status: married, filing jointly.
- Dependents: 1.
- Paid wages by 2 employers, counting any job that ended during the year.
- Earned interest from a bank, credit union, or bonds.
- Received dividends from stocks or mutual funds.
- Received unemployment benefits.
- Bought health insurance through HealthCare.gov or a state exchange.
- Has a mortgage on their home.
- Paid interest on a student loan.
- Owns a share of 2 partnerships, small companies, estates, or trusts.
- Rented out a property.
- Won money gambling.
- Gave money or goods to charity.
- Bought an electric vehicle; signed the contract and made a payment on 2026-01-26.
- Took the standard deduction on last year's return.

Changed since last year:

- New this year: unemployment benefits.
- New this year: a health plan bought through HealthCare.gov or a state exchange.
- New this year: gambling winnings.
- New this year: an electric vehicle purchase.

## H051 (train, medium)

This year:

- Filing status: single.
- Dependents: 1.
- Paid wages by 1 employer, counting any job that ended during the year.
- Did freelance or gig work, or ran a small business of their own.
- Some customers paid for that work through a payment app or an online marketplace.
- Received dividends from stocks or mutual funds.
- Received Social Security benefits.
- Sent the IRS tax payments during the year, apart from paycheck withholding.
- Took the standard deduction on last year's return.

Changed since last year:

- New this year: freelance, gig, or small business work, with some customers paying through an app or online marketplace.
- New this year: Social Security benefits.

## H053 (dev, complex)

This year:

- Filing status: married, filing jointly.
- Dependents: 1.
- Paid wages by 2 employers, counting any job that ended during the year.
- Earned interest from a bank, credit union, or bonds.
- Received dividends from stocks or mutual funds.
- Sold some stocks, bonds, or mutual funds.
- Bought health insurance through HealthCare.gov or a state exchange.
- Paid college tuition or fees for themselves or someone they support.
- Paid for child care or other dependent care so they could work.
- Owns a share of 2 partnerships, small companies, estates, or trusts.
- Rented out a property.
- Was paid overtime at work.
- Installed solar panels or similar clean energy equipment; the work was finished in 2026.
- Took the standard deduction on last year's return.

Changed since last year:

- Employers paying wages: 1 last year, 2 this year.
- New this year: a health plan bought through HealthCare.gov or a state exchange.
- New this year: college tuition payments.
- New this year: a clean energy installation.

## H056 (dev, medium)

This year:

- Filing status: head of household.
- Dependents: 1.
- Paid wages by 1 employer, counting any job that ended during the year.
- Did freelance or gig work, or ran a small business of their own.
- Made energy-saving home upgrades, such as windows, insulation, or a heat pump, put in during 2026.
- Took the standard deduction on last year's return.

Changed since last year:

- New this year: freelance, gig, or small business work.
- New this year: energy-saving home upgrades.

## H066 (dev, complex)

This year:

- Filing status: married, filing jointly.
- Dependents: none.
- Paid wages by 2 employers, counting any job that ended during the year.
- Received dividends from stocks or mutual funds.
- Sold some stocks, bonds, or mutual funds.
- Took money out of a retirement account.
- Bought health insurance through HealthCare.gov or a state exchange.
- Has a mortgage on their home.
- Bought a home this year.
- Paid interest on a student loan.
- Owns a share of 1 partnership, small company, estate, or trust.
- Sent the IRS tax payments during the year, apart from paycheck withholding.
- Gave money or goods to charity.
- Installed solar panels or similar clean energy equipment; the work was finished in 2026.
- Itemized deductions on last year's return.

Changed since last year:

- Employers paying wages: 3 last year, 2 this year.
- New this year: a retirement account withdrawal.
- New this year: a health plan bought through HealthCare.gov or a state exchange.
- New this year: a home purchase.
- New this year: a clean energy installation.

## H067 (dev, complex)

This year:

- Filing status: head of household.
- Dependents: 2.
- Paid wages by 1 employer, counting any job that ended during the year.
- Did freelance or gig work, or ran a small business of their own.
- Some customers paid for that work through a payment app or an online marketplace.
- Sold or traded cryptocurrency.
- Took money out of a retirement account.
- Got a state income tax refund this year.
- Put money into a health savings account directly, outside their paycheck.
- Has a mortgage on their home.
- Owns a share of 1 partnership, small company, estate, or trust.
- Rented out a property.
- Gave money or goods to charity.
- Pays interest on a loan for a new car bought for personal use.
- Made energy-saving home upgrades, such as windows, insulation, or a heat pump, put in during 2026.
- Took the standard deduction on last year's return.

Changed since last year:

- New this year: freelance, gig, or small business work, with some customers paying through an app or online marketplace.
- New this year: cryptocurrency sales or trades.
- New this year: a retirement account withdrawal.
- Last year only: college tuition payments.
- New this year: energy-saving home upgrades.

## H068 (train, simple)

This year:

- Filing status: married, filing jointly.
- Dependents: 3.
- Paid wages by 2 employers, counting any job that ended during the year.
- Took the standard deduction on last year's return.

Changed since last year:

- Nothing changed since last year.

## H079 (dev, medium)

This year:

- Filing status: married, filing jointly.
- Dependents: 2.
- Paid wages by 2 employers, counting any job that ended during the year.
- Did freelance or gig work, or ran a small business of their own.
- Received Social Security benefits.
- Paid interest on a student loan.
- Owns a share of 1 partnership, small company, estate, or trust.
- Took the standard deduction on last year's return.

Changed since last year:

- New this year: freelance, gig, or small business work.
- New this year: Social Security benefits.

## H080 (train, medium)

This year:

- Filing status: single.
- Dependents: none.
- Paid wages by 1 employer, counting any job that ended during the year.
- Paid interest on a student loan.
- The IRS gives them a six-digit identity protection number each year.
- Made energy-saving home upgrades, such as windows, insulation, or a heat pump, put in during 2026.
- Took the standard deduction on last year's return.

Changed since last year:

- New this year: energy-saving home upgrades.

## H081 (dev, simple)

This year:

- Filing status: married, filing separately.
- Dependents: 2.
- Paid wages by 1 employer, counting any job that ended during the year.
- Took the standard deduction on last year's return.

Changed since last year:

- Nothing changed since last year.

## H082 (dev, simple)

This year:

- Filing status: single.
- Dependents: 2.
- Paid wages by 2 employers, counting any job that ended during the year.
- Took the standard deduction on last year's return.

Changed since last year:

- Nothing changed since last year.

## H084 (dev, medium)

This year:

- Filing status: single.
- Dependents: none.
- Paid wages by 2 employers, counting any job that ended during the year.
- Has a mortgage on their home.
- Bought a home this year.
- The IRS gives them a six-digit identity protection number each year.
- Bought an electric vehicle; signed the contract and made a payment on 2026-03-13.
- Took the standard deduction on last year's return.

Changed since last year:

- New this year: a mortgage.
- New this year: a home purchase.
- New this year: an electric vehicle purchase.

## H085 (train, medium)

This year:

- Filing status: head of household.
- Dependents: 1.
- Paid wages by 2 employers, counting any job that ended during the year.
- Sold some stocks, bonds, or mutual funds.
- Took money out of a retirement account.
- Got a state income tax refund this year.
- Put money into a health savings account directly, outside their paycheck.
- Took the standard deduction on last year's return.

Changed since last year:

- New this year: a retirement account withdrawal.
- Last year only: a health plan bought through HealthCare.gov or a state exchange.

## H086 (dev, medium)

This year:

- Filing status: single.
- Dependents: 2.
- Paid wages by 2 employers, counting any job that ended during the year.
- Did freelance or gig work, or ran a small business of their own.
- Some customers paid for that work through a payment app or an online marketplace.
- Sold some stocks, bonds, or mutual funds.
- Owns a share of 1 partnership, small company, estate, or trust.
- Took the standard deduction on last year's return.

Changed since last year:

- New this year: freelance, gig, or small business work, with some customers paying through an app or online marketplace.

## H087 (train, complex)

This year:

- Filing status: married, filing separately.
- Dependents: none.
- Paid wages by 3 employers, counting any job that ended during the year.
- Did freelance or gig work, or ran a small business of their own.
- Some customers paid for that work through a payment app or an online marketplace.
- Sold or traded cryptocurrency.
- Got a state income tax refund this year.
- Has a mortgage on their home.
- Sold a home this year.
- Paid college tuition or fees for themselves or someone they support.
- Rented out a property.
- Was paid overtime at work.
- Bought an electric vehicle; signed the contract and made a payment on 2026-04-09.
- Itemized deductions on last year's return.

Changed since last year:

- New this year: freelance, gig, or small business work, with some customers paying through an app or online marketplace.
- New this year: cryptocurrency sales or trades.
- New this year: a home sale.
- New this year: college tuition payments.
- New this year: an electric vehicle purchase.

## H088 (dev, simple)

This year:

- Filing status: married, filing jointly.
- Dependents: 3.
- Paid wages by 1 employer, counting any job that ended during the year.
- Took the standard deduction on last year's return.

Changed since last year:

- Nothing changed since last year.

## H093 (dev, medium)

This year:

- Filing status: married, filing jointly.
- Dependents: 3, one of them new this year (a birth, an adoption, or someone newly supported).
- Paid wages by 3 employers, counting any job that ended during the year.
- Took money out of a retirement account.
- Won money gambling.
- Was paid overtime at work.
- Took the standard deduction on last year's return.

Changed since last year:

- Dependents: 2 last year, 3 this year.
- New this year: a dependent who joined the household.
- New this year: a retirement account withdrawal.
- New this year: gambling winnings.
