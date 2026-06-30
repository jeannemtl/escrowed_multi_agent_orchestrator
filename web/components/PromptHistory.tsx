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
  return (
    <section className="prompt-history">
      <span className="hist-label">History</span>
      {items.length === 0 ? (
        <span className="hist-empty">Submit a prompt to start</span>
      ) : (
        items.map((item) => {
          const isActive = item.id === activeId;
          return (
            <div
              key={item.id}
              className={`hist-item ${isActive ? 'active' : ''}`}
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
              <span>{item.text}</span>
              <span className="hist-date">{item.date}</span>
            </div>
          );
        })
      )}
    </section>
  );
}
