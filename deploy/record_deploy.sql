-- Records one deploy of main. Written before the pipeline runs, so any run
-- that starts after DEPLOYED_AT used this commit's code. The workflow checks
-- every value against a strict pattern before passing it here.
INSERT INTO {{DB}}.DEPLOY.CHANGE_HISTORY (COMMIT_SHA, PR_NUMBER, EVENT, ACTOR, RUN_ID, RUN_ATTEMPT, RUN_URL)
SELECT '{{COMMIT}}', TRY_TO_NUMBER('{{PR}}'), '{{EVENT}}', '{{ACTOR}}', '{{RUN_ID}}', {{RUN_ATTEMPT}}, '{{RUN_URL}}';
