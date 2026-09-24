ALTER TABLE `services` ADD `source_id` text;--> statement-breakpoint
ALTER TABLE `sources` ADD `agent_write` integer DEFAULT true NOT NULL;