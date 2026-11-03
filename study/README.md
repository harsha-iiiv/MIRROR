# MIRROR impact study kit

**Status: not yet run.** No participant has been recruited and no session has
taken place. Everything under `fixtures/` is synthetic and labelled so in every
record; it exists only to test the tools.

The question: do robotics engineers and integrators who revise handling
constraints for kitting tasks reach a valid task specification faster and with
fewer wrong-scope edits when they correct MIRROR in natural language than when
they edit the structured specification by hand?

| File | Purpose |
|---|---|
| [PROTOCOL.md](PROTOCOL.md) | Design, participants, conditions, counterbalancing, metrics, analysis plan |
| [FACILITATOR.md](FACILITATOR.md) | Session script, timing, what to say and not say |
| [consent_form.md](consent_form.md) | Participant information and consent |
| [DATA.md](DATA.md) | Data minimisation, storage, retention, withdrawal |
| [INSTRUMENTATION.md](INSTRUMENTATION.md) | Event log schema the studio emits during sessions |
| [task_packet/](task_packet/) | Participant task sheets and the machine-readable task file with acceptance criteria |
| `tools/studykit.py` | Assignment, validation, export and analysis (`python study/tools/studykit.py -h`) |
| `fixtures/` | Synthetic event logs for testing the tools; never study data |

Running the study needs the owner's approval to contact participants, an
appropriate ethics or review decision where the participants' organisations
require one, and the studio's event logging (requested from the studio track).
