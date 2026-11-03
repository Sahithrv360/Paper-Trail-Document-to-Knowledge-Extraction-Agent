import React from 'react';
import { ShieldCheck, AlertCircle, AlertTriangle } from 'lucide-react';

export default function ConfidenceBadge({ confidence, showIcon = true, status = "", basis = "" }) {
  if (confidence === null || confidence === undefined || !Number.isFinite(Number(confidence))) {
    return <span className="badge badge-gray" title="No confidence score was provided">Not scored</span>;
  }
  const pct = Math.round(confidence * 100);
  if (basis === 'rule_based_quality_proxy') {
    const scoreClass = pct >= 80 ? 'badge-green' : pct >= 60 ? 'badge-amber' : 'badge-red';
    return <span className={`badge ${scoreClass}`} title="Rule-based extraction quality score; not a statistically calibrated probability">{pct}% score</span>;
  }
  let badgeClass = "badge-green";
  let Icon = ShieldCheck;
  let label = `${pct}% High`;

  if (status === "Unsupported" || status === "Disputed" || pct < 70) {
    badgeClass = "badge-red";
    Icon = AlertTriangle;
    label = `${pct}% Flagged`;
  } else if (status === "Needs Review" || status === "Possible" || pct < 85) {
    badgeClass = "badge-amber";
    Icon = AlertCircle;
    label = `${pct}% Review`;
  }

  return (
    <span className={`badge ${badgeClass}`} title={`Confidence Score: ${pct}%`}>
      {showIcon && <Icon size={12} />}
      <span>{label}</span>
    </span>
  );
}
