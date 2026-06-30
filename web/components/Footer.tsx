'use client';

interface FooterProps {
  onHelp: () => void;
}

export default function Footer({ onHelp }: FooterProps) {
  return (
    <footer className="app-footer">
      <span className="footer-copy">© Cerebrain, 2026</span>
      <div className="footer-links">
        <a onClick={onHelp}>Home</a>
        <a onClick={onHelp}>About</a>
        <a onClick={onHelp}>Privacy</a>
        <a onClick={onHelp}>Terms &amp; Conditions</a>
        <a onClick={onHelp}>Account</a>
      </div>
      <span className="footer-spacer" />
    </footer>
  );
}
