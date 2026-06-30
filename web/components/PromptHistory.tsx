'use client';

interface PromptHistoryProps {
  items?: { id: string; text: string; date: string }[];
  activeId?: string;
  onSelect?: (id: string) => void;
}

export default function PromptHistory({
  items = [],
  activeId,
  onSelect,
}: PromptHistoryProps) {
  // The active project is rendered in the dedicated ACTIVE block (right side).
  const active = items.find((it) => it.id === activeId);
  const past = items.filter((it) => it.id !== activeId);

  return (
    <section className="prompt-history">
      {/* left decorative dots */}
      <div className="hist-end">
        <span className="hist-dot d30" />
        <span className="hist-dot d55" />
      </div>

      {items.length === 0 ? (
        <span className="hist-empty">Submit a prompt to start</span>
      ) : (
        past.map((item) => (
          <div
            key={item.id}
            className="hist-item"
            onClick={() => onSelect && onSelect(item.id)}
            role={onSelect ? 'button' : undefined}
            tabIndex={onSelect ? 0 : undefined}
            onKeyDown={(e) => {
              if (onSelect && (e.key === 'Enter' || e.key === ' ')) {
                e.preventDefault();
                onSelect(item.id);
              }
            }}
          >
            <span className="hist-item-text">{item.text}</span>
            <span className="hist-item-date">{item.date}</span>
          </div>
        ))
      )}

      {/* ACTIVE block */}
      <div className="hist-active">
        {active ? (
          <>
            <div className="hist-active-top">
              <span className="hist-active-label">ACTIVE</span>
              <span className="hist-active-date">{active.date || 'TODAY'}</span>
            </div>
            <span className="hist-active-text">{active.text}</span>
          </>
        ) : (
          <>
            <div className="hist-active-top">
              <span className="hist-active-label">ACTIVE</span>
              <span className="hist-active-date">—</span>
            </div>
            <span className="hist-active-text">Awaiting a prompt</span>
          </>
        )}
      </div>

      {/* right decorative dots */}
      <div className="hist-end right">
        <span className="hist-dot d55" />
        <span className="hist-dot d100" />
      </div>

      {/* triangle pointer */}
      <div className="hist-triangle" />
    </section>
  );
}
