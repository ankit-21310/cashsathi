import Link from "next/link";

import { Brand } from "@/components/brand";

const safeguards = [
  "72-hour reminder cooldown",
  "Human approval above ₹50,000",
  "Disputes stop automation",
  "Payment always needs confirmation",
];

export default function Home() {
  return (
    <main className="landing">
      <nav className="landing-nav">
        <Brand />
        <Link className="button button-secondary" href="/login">Sign in</Link>
      </nav>
      <section className="hero">
        <div className="eyebrow">Private product preview · India</div>
        <h1>Turn invoice follow-up into a controlled operating loop.</h1>
        <p>
          A focused receivables workspace for agencies and consultants: understand the invoice,
          choose a safe next action, and preserve the evidence behind every decision.
        </p>
        <div className="hero-actions">
          <Link className="button button-primary" href="/login">Open secure preview</Link>
          <a className="text-link" href="#safety">See the safety boundary →</a>
        </div>
      </section>
      <section className="workflow-grid" aria-label="Product workflow">
        <article><span>01</span><h2>Understand</h2><p>Convert a real invoice into user-confirmed structured facts.</p></article>
        <article><span>02</span><h2>Decide</h2><p>Combine state, history, and constrained AI recommendations.</p></article>
        <article><span>03</span><h2>Control</h2><p>Apply business policy before any real-world action executes.</p></article>
        <article><span>04</span><h2>Prove</h2><p>Keep an append-oriented trail from decision to verified outcome.</p></article>
      </section>
      <section className="safety-panel" id="safety">
        <div>
          <div className="eyebrow">Responsible autonomy</div>
          <h2>AI handles the repetitive queue. People own policy and exceptions.</h2>
        </div>
        <ul>{safeguards.map((item) => <li key={item}>{item}</li>)}</ul>
      </section>
      <section className="pricing-panel" aria-label="Founder Recovery Plan">
        <div><div className="eyebrow">Founder pricing hypothesis</div><h2>₹299 one-time</h2><p>Up to 10 confirmed invoices, AI tracking, controlled Gmail reminders, dashboard, and action history.</p></div>
        <div><strong>Manual payment verification</strong><p>No subscription and no automatic billing. Limited preview availability.</p><Link className="button button-primary" href="/login">Open secure preview</Link></div>
      </section>
      <footer>Internal codename: CashSathi · Public name pending formal clearance</footer>
    </main>
  );
}
