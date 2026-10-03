export type Job = {
  id: string;
  url: string;
  title: string;
  company: string;
  location: string;
  ats: string;
  status: string;
  score: number | null;
  reason: string;
  resume_id: string | null;
  demo: boolean;
  created_at: string;
};
export type Run = {
  id: string;
  job_id: string;
  state: string;
  mode: string;
  elapsed: number;
  cost: number;
  model_calls: number;
  reason: string;
  created_at: string;
  finished_at: string | null;
  receipt: Record<string, unknown>;
};
export type Resume = {
  id: string;
  name: string;
  roles: string[];
  sha256: string;
  created_at: string;
};
export type Review = {
  id: string;
  run_id: string;
  question: string;
  options: string[];
  reason: string;
  key: string;
};
export type Source = {
  id: string;
  name: string;
  kind: string;
  url: string;
  cron: string;
  enabled: boolean;
  auto_queue: boolean;
  found: number;
  error: string;
  last_run: string | null;
  next_run: string;
};
export type Address = {
  line1: string;
  line2: string;
  city: string;
  state: string;
  postal_code: string;
  country: string;
  county: string;
};
export type WorkEntry = {
  company: string;
  title: string;
  location: string;
  start: string;
  end: string;
  current: boolean;
  description: string;
};
export type EducationEntry = {
  school: string;
  degree: string;
  field_of_study: string;
  start: string;
  end: string;
  gpa: string;
};
export type KnowledgeEntry = {
  id: string;
  question: string;
  answer: string;
  options: string[];
  scope: string;
  source: string;
  updated_at: string;
};
export type AccountRow = {
  id: string;
  realm: string;
  ats: string;
  email: string;
  state: string;
  resets: number;
  last_error: string;
  updated_at: string;
  password_current: boolean;
};
export type AccountSummary = {
  email: string;
  password_configured: boolean;
  password_problems: string[];
  accounts: AccountRow[];
};
export type Profile = {
  name: string;
  email: string;
  phone: string;
  location: string;
  linkedin: string;
  website: string;
  github: string;
  first_name: string;
  last_name: string;
  middle_name: string;
  preferred_name: string;
  phone_country_code: string;
  phone_type: string;
  address: Address;
  work: WorkEntry[];
  education: EducationEntry[];
  skills: string[];
  languages: string[];
  referral_source: string;
  salary_expectation: string;
  availability: string;
  auto_accept_consents: boolean;
  facts: Record<string, unknown>;
  evidence: { id: string; text: string }[];
  target_roles: string[];
  target_locations: string[];
  excluded_keywords: string[];
  minimum_fit: number;
  decline_demographics: boolean;
  approved_consents: string[];
  approved_answers: Record<string, string>;
};
export type Snapshot = {
  jobs: Job[];
  runs: Run[];
  resumes: Resume[];
  reviews: Review[];
  sources: Source[];
  profile: Profile;
  control: { paused: boolean; auto_submit: boolean };
  stats: {
    confirmed_today: number;
    submissions_reserved: number;
    spend_today: number;
    states: Record<string, number>;
    average_seconds: number;
  };
  config: {
    workers: number;
    timeout: number;
    daily_limit: number;
    budget: number;
    timezone: string;
    model: string;
    demo: boolean;
    workers_enabled: boolean;
    mimo: boolean;
    telegram: boolean;
    capsolver: boolean;
    engine: string;
    multipage_timeout: number;
    account: boolean;
    account_email: string;
    imap: boolean;
    telegram_wait: number;
    auto_requeue: boolean;
  };
  ats: { id: string; name: string; tier: string; guidance: string }[];
};
export type Detail = {
  run: Run;
  events: {
    id: number;
    created_at: string;
    kind: string;
    message: string;
    data: unknown;
  }[];
  answers: Record<
    string,
    { label: string; answer: string; evidence_ids: string[]; verified: boolean }
  >;
  screenshot: boolean;
};
