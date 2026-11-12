-- Runs the pipeline once after a deploy: the whole task graph (AWAIT returns
-- an error if any task fails), then the dynamic table. The root task stays
-- suspended; EXECUTE TASK runs it without resuming it.
EXECUTE TASK {{DB}}.ORCHESTRATION.LOAD_ORDERS AWAIT;
ALTER DYNAMIC TABLE {{DB}}.MART.CUSTOMER_LTV REFRESH;

UPDATE {{DB}}.DEPLOY.CHANGE_HISTORY
   SET PIPELINE_STATUS = 'SUCCEEDED', PIPELINE_FINISHED_AT = SYSDATE()
 WHERE RUN_ID = '{{RUN_ID}}' AND RUN_ATTEMPT = {{RUN_ATTEMPT}};
