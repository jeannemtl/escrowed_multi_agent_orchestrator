'use client';

interface FooterProps {
  onHelp: () => void;
}

export default function Footer({ onHelp }: FooterProps) {
  return (
    <footer className="app-footer">
      <span>© 2026 Cerebrain · Multi-Agent Orchestrator</span>
      <div className="footer-links">
        <a onClick={onHelp}>Help</a>
        <a
          href="https://hermes-agent.nousresearch.com/docs"
          target="_blank"
          rel="noreferrer"
        >
          Docs
        </a>
        <a>Privacy</a>
      </div>
    </footer>
  );
}
