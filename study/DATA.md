# Data minimisation

* **Identifiers.** Participant codes only (P01...). The consent register that
  maps codes to names stays with the study owner, offline, never with the logs.
* **Event logs** (`INSTRUMENTATION.md`) carry codes, timestamps, specification
  states (rules and object kinds, no personal content), outcomes and provenance.
  Correction text is logged only when `consent.text` is true for the session;
  otherwise only its character count.
* **Audio** only with `consent.audio`; stored with the raw logs, deleted after
  transcript check (30 days).
* **No video of people, no screen recording, no IP addresses, no device ids.**
* **Storage.** Raw logs in a private store. The repository receives only the
  exported per-person table without free text (`studykit.py export
  --drop-text`), after the study.
* **Retention.** Raw logs at most 90 days after the study; aggregated results
  kept. Withdrawal deletes that participant's raw logs and removes their rows
  before publication.
* **Synthetic data.** Every fixture record has `"synthetic": true`; the tools
  refuse to combine synthetic and real records, and reports built from
  synthetic data say so in their first line.
