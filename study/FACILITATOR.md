# Facilitator script

1. **Before** (10 min): confirm consent form signed; assign pseudonymous id;
   start the studio in study mode with `MIRROR_STUDY_LOG=<private path>/<id>.jsonl`;
   check the studio commit and task-packet sha256 match the study record.
2. **Introduction** (read): "We are testing the software, not you. Think aloud
   if you like. You can stop at any time. I can explain the interface but not
   what to type."
3. **Training** (10 min): recorded tour, then practice task T0 in each condition.
4. **Timed tasks** (2 x 3 tasks, 8 minutes each): hand over the task sheet;
   start the task in the studio (this logs `task_start`); the participant says
   "done" or the limit passes (logs `task_end`).
5. **Ratings** after each condition (2 questions, optional comment).
6. **T7 reteach** (exploratory, 2 x 8 minutes).
7. **After**: stop logging; copy the log to the private study store; never
   into the repository.

Do not suggest wording, do not reveal acceptance criteria, do not fix the
specification for the participant. Note any intervention you had to make
(technical problem, explanation) in the session notes; interventions are
reported per session.
