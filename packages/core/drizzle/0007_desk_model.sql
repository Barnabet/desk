-- Repair Desk agents whose project model was changed before settings updates
-- also updated the agent. Threads keep their individually assigned models.
UPDATE agents
SET model = (SELECT json_extract(settings, '$.desk_model') FROM projects WHERE projects.id = agents.project_id)
WHERE role = 'desk'
  AND EXISTS (
    SELECT 1 FROM projects
    WHERE projects.id = agents.project_id
      AND json_extract(settings, '$.desk_model') IS NOT NULL
      AND json_extract(settings, '$.desk_model') != agents.model
  );
