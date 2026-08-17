CREATE TYPE "CustomEventOperation" AS ENUM ('union', 'intersection');

CREATE TABLE "custom_events" (
    "id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "name" TEXT NOT NULL,
    "description" TEXT,
    "operation" "CustomEventOperation" NOT NULL,
    "eventNames" TEXT[] NOT NULL,
    "projectId" TEXT NOT NULL,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "custom_events_pkey" PRIMARY KEY ("id")
);

CREATE UNIQUE INDEX "custom_events_name_projectId_key"
ON "custom_events"("name", "projectId");

ALTER TABLE "custom_events"
ADD CONSTRAINT "custom_events_projectId_fkey"
FOREIGN KEY ("projectId") REFERENCES "projects"("id")
ON DELETE CASCADE ON UPDATE CASCADE;
