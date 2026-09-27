import { useMemo, useState } from 'react';
import { narrate, sentCalls } from '@desk/ui-core';
import { Sheet } from '../../components/Sheet';
import { useTranscript, type SessionState } from '../../state/session';
import { Transcript, type Depth } from '../../threads/Transcript';

/** A step agent's transcript (the Threads tab's view), which only the run view opens: step agents are not threads. */
export function StepTranscript(o: { projectId: string; s: SessionState; agentId: string; title: string; onClose(): void }) {
  const transcript = useTranscript(o.s, o.projectId, o.agentId);
  const sent = useMemo(() => sentCalls(o.s.messages, o.agentId), [o.s.messages, o.agentId]);
  const rows = useMemo(() => narrate(transcript.entries, sent), [transcript.entries, sent]);
  const [selected, setSelected] = useState<number | null>(null);
  const [depth, setDepth] = useState<Depth>('narrative');
  return (
    <Sheet title={`${o.title} · transcript`} onClose={o.onClose} width={760}>
      <div className="auto-transcript">
        <Transcript
          projectId={o.projectId}
          threadId={o.agentId}
          rows={rows}
          entries={transcript.entries}
          reviewRounds={0}
          messages={o.s.messages}
          sent={sent}
          onPair={() => {}}
          selected={selected}
          onSelect={setSelected}
          depth={depth}
          onDepth={setDepth}
          actions={null}
          composer={{ kind: 'off', hint: 'A step agent takes no messages. Answer its approvals here, or stop the step.' }}
        />
      </div>
    </Sheet>
  );
}
