# Food Safety Inspection Analysis & AI Assistant

Finds Chicago food establishments with recurring inspection problems, tests whether failure patterns are
statistically unusual, and answers questions about an establishment with its inspection record and cited
food-code provisions.

## Stakeholder
A city food-safety department deciding which establishments to prioritise and what to check on the next visit.

## Data
**Run used for the results below:** the City of Chicago's own published copy of its food-inspection records,
**97,432 inspections, Jan 2010 - Dec 2014**, from github.com/Chicago/food-inspections-evaluation
(`DATA/13_food_inspections.Rds`; `src/rds_reader.py` decodes it without R, including data.table date columns):
```bash
git clone --depth 1 https://github.com/Chicago/food-inspections-evaluation /tmp/chi
python src/convert_rds.py /tmp/chi/DATA/13_food_inspections.Rds
export P3_CODE_ERA_START=2000-01-01     # 2010-2014 uses one (older) violation numbering throughout
```
Regulatory corpus for that run: `src/build_code_corpus.py` extracts the City's standard code-requirement sentences
that inspectors quote in their comments (95 sentences across 43 violation sections, 2010-2013 reports only).

**Current data:** [Chicago Food Inspections portal](https://data.cityofchicago.org/Health-Human-Services/Food-Inspections/4ijn-s7e5)
(`python src/download.py`) plus the official code PDFs saved in `data/regulations/`.

## Method
| Question | Approach |
|---|---|
| Who fails repeatedly, which violations recur? | DuckDB SQL: clean inspections, split the `Violations` text into one row per citation, establishment summaries, repeat-violation table. Recurrence lift = P(cited again next inspection given cited now) / P(cited given not cited now). Violation analysis uses one numbering era at a time (portal data: post-1 July 2018 code; 2010-2014 run: the older code throughout). |
| Is a failure associated with failing the next inspection? | Consecutive **routine (canvass)** inspections only; re-inspections excluded because failures trigger them. Crude risk/odds ratios with chi-square; logistic regression with facility type, risk, year, time gap, prior count, establishment-clustered SEs; then adding the establishment's fail rate on its *other* inspections to separate persistent weakness from a recent-failure effect; dose-response over the last two inspections. |
| Which establishments are statistically unusual? | Expected failures from peer rates (facility type x risk x year x inspection type); one-sided Poisson test; Benjamini-Hochberg FDR 5%. |
| Can the system answer with evidence? | Regulations split by section headers (FDA `3-501.16`, Chicago `7-38-005`), indexed with BM25 + embeddings (reciprocal rank fusion). The assistant builds context from SQL history, peer statistics and retrieved provisions, cites `[I-<id>]` and `[R<n>]`, and runs a grounding check on every answer. Violation codes are auto-mapped to provisions in `outputs/violation_regulation_map.csv` for human verification. |

## Results (real data, 2010-2014; `outputs/*.json`)
- 97,429 inspections, 86,229 with a pass/fail outcome, 26,615 establishments, 363,144 violation citations.
- Routine inspection after a failed one: 27.0% fail vs 16.6% after a pass (risk ratio 1.63, 25,169 pairs).
  Adjusted OR 1.84 (95% CI 1.70-1.99); 1.74 (1.61-1.89) after controlling for the establishment's record on its
  other inspections. Dose-response: 14.7% / 21.9% / 31.0% fail when 0 / 1 / 2 of the last two failed.
- Most repeated violation: #34 floors (repeated at 11,368 establishments; 69% recurrence vs 47% baseline).
  Highest recurrence lift: #18 rodent/insect openings (2.3x).
- No establishment had statistically unusual failure counts after FDR correction (7,779 tested; 198 at uncorrected
  p<0.05 vs 389 expected by chance). With ~6 inspections per establishment over five years the test has little power.
- Observation-to-provision retrieval on 3,000 held-out 2014 observations: case-based retrieval top-1 0.885, top-3
  0.970 (0.882 / 0.968 excluding observations that quote a code section); BM25 over code text 0.30 / 0.50;
  most-frequent baseline 0.14 / 0.36.

## Evaluation
- `eval/reg_gold.csv`: questions + keywords a correct provision must contain; hit@1/3/5 and MRR per retriever.
  Extend it for the documents you ingest.
- With an API key: share of figures in generated answers found in context, and validity of record/regulation citations.

## Run
```bash
pip install -r requirements.txt
python src/download.py
python src/build_db.py
python src/stats_analysis.py      # outputs/stats_results.json
python src/build_code_corpus.py   # only for the 2010-2014 run without code PDFs
python src/regs.py                # index + violation->regulation map
python src/cases.py               # case-based index (observation -> violation)
python src/eval_observation_retrieval.py
export ANTHROPIC_API_KEY=...      # optional
python src/eval_rag.py            # outputs/rag_results.json
streamlit run app/app.py
python tests/smoke_test.py
```

## Limitations
License number is the establishment key (ownership changes not merged). Inspection frequency depends on risk and
complaints. Inspectors vary. Associations are not causes. Statistical flags are prompts for review, not findings of
non-compliance. Regulatory answers depend on the ingested documents and need inspector confirmation.
