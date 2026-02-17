---
toc: false,
pager: false

---

<link rel="stylesheet" href="styles/styles.css">

<style>

.hero {
  display: flex;
  flex-direction: column;
  align-items: center;
  font-family: var(--sans-serif);
  margin: 4rem 0 2rem;
  text-wrap: balance;
  text-align: center;
}

.hero a:link,
.hero a:visited {
  color: var(--ml-color-900);
  text-decoration: none;
}

.hero a:hover,
.hero a:active {
  color: var(--ml-color-700);
  text-decoration: none !important;
}

.hero h1 {
  margin: 2rem 0;
  max-width: none;
  font-size: 14vw;
  font-weight: 900;
  line-height: 1;
  color: var(--ml-color-700);
}

.hero h2 {
  margin: 0;
  max-width: 34em;
  font-size: 20px;
  font-style: initial;
  font-weight: 500;
  line-height: 1.5;
  color: var(--ml-color-900);
}

@media (min-width: 640px) {
  .hero h1 {
    font-size: 90px;
  }
}

.intro {
  max-width: 640px;
  margin: 0 auto 3rem;
  text-align: center;
  font-size: 1.05rem;
  line-height: 1.7;
  color: var(--ml-color-900);
}

.studies-section {
  margin: 0 auto 3rem;
  max-width: 900px;
}

.studies-section h3 {
  text-transform: uppercase;
  letter-spacing: 0.05em;
  font-size: 1.125rem;
  font-weight: 700;
  color: var(--ml-color-900);
  margin: 0 0 1.25rem;
  text-align: center;
}

.study-cards {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 1rem;
}

@media (max-width: 640px) {
  .study-cards {
    grid-template-columns: 1fr;
  }
}

.study-card {
  background: var(--ml-color-200);
  border-radius: 0.5rem;
  padding: 1.25rem 1.5rem;
  display: flex;
  flex-direction: column;
  text-decoration: none !important;
  border: 1px solid transparent;
  transition: border-color 0.15s, background-color 0.15s;
}

.study-card:hover {
  border-color: var(--ml-color-700);
  background: var(--ml-color-150);
}

a.study-card,
a.study-card:link,
a.study-card:visited {
  color: var(--ml-color-900);
  text-decoration: none;
  border-bottom: none;
}

.study-card-name {
  font-weight: 700;
  font-size: 1rem;
  margin: 0 0 0.5rem;
  color: var(--ml-color-900);
}

.study-card-desc {
  font-size: 0.88rem;
  line-height: 1.55;
  color: var(--ml-color-800);
  margin: 0 0 0.75rem;
  flex: 1;
}

.study-card-meta {
  font-size: 0.75rem;
  letter-spacing: 0.04em;
  color: var(--ml-color-700);
  text-transform: uppercase;
  font-weight: 600;
}

.how-section {
  max-width: 740px;
  margin: 0 auto 4rem;
}

.how-section h3 {
  text-transform: uppercase;
  letter-spacing: 0.05em;
  font-size: 1.125rem;
  font-weight: 700;
  color: var(--ml-color-900);
  margin: 0 0 1.25rem;
  text-align: center;
}

.how-steps {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 1.25rem;
  text-align: center;
}

@media (max-width: 640px) {
  .how-steps {
    grid-template-columns: 1fr;
  }
}

.how-step {
  padding: 1.25rem 1rem;
}

.how-step-number {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 2rem;
  height: 2rem;
  border-radius: 50%;
  background: var(--ml-color-100);
  color: var(--ml-color-700);
  font-weight: 700;
  font-size: 0.95rem;
  margin-bottom: 0.75rem;
}

.how-step-title {
  font-weight: 700;
  font-size: 0.95rem;
  color: var(--ml-color-900);
  margin: 0 0 0.35rem;
}

.how-step-desc {
  font-size: 0.85rem;
  line-height: 1.5;
  color: var(--ml-color-800);
  margin: 0;
}

</style>

<div class="hero">
  <h1>Metalens</h1>
  <h2>An interactive tool to explore scientific evidence</h2>
</div>

<div class="intro">
  <p>Science produces thousands of studies on every topic. A single study rarely gives the full picture &mdash; but a <strong>meta-analysis</strong> combines the results of many studies to show what the evidence actually says. Metalens lets you explore these meta-analyses interactively.</p>
</div>

<div class="studies-section">
  <h3>Explore meta-studies</h3>
  <div class="study-cards">
    <a class="study-card" href="/studies/dat.axfors2021">
      <div class="study-card-name">Hydroxychloroquine &amp; COVID-19</div>
      <div class="study-card-desc">Does hydroxychloroquine or chloroquine reduce mortality in COVID-19 patients? Results from 33 randomized trials.</div>
      <div class="study-card-meta">33 trials &middot; Medicine</div>
    </a>
    <a class="study-card" href="/studies/dat.bangertdrowns2004">
      <div class="study-card-name">Writing-to-Learn Interventions</div>
      <div class="study-card-desc">Do writing-focused teaching methods improve academic achievement? Results from 48 school-based studies.</div>
      <div class="study-card-meta">48 studies &middot; Education</div>
    </a>
    <a class="study-card" href="/studies/dat.molloy2014">
      <div class="study-card-name">Conscientiousness &amp; Medication</div>
      <div class="study-card-desc">Is the personality trait of conscientiousness linked to better medication adherence? Results from 16 studies.</div>
      <div class="study-card-meta">16 studies &middot; Psychology</div>
    </a>
    <a class="study-card" href="/studies/dat.aloe2013">
      <div class="study-card-name">Supervision Quality</div>
      <div class="study-card-desc">How does supervision quality relate to job outcomes in social and mental health workers? Results from 5 studies.</div>
      <div class="study-card-meta">5 studies &middot; Social work</div>
    </a>
  </div>
</div>

<div class="how-section">
  <h3>How it works</h3>
  <div class="how-steps">
    <div class="how-step">
      <div class="how-step-number">1</div>
      <div class="how-step-title">Pick a study</div>
      <p class="how-step-desc">Choose a meta-analysis above or upload your own dataset.</p>
    </div>
    <div class="how-step">
      <div class="how-step-number">2</div>
      <div class="how-step-title">Filter</div>
      <p class="how-step-desc">Narrow down results by study type, year, sample size, and more.</p>
    </div>
    <div class="how-step">
      <div class="how-step-number">3</div>
      <div class="how-step-title">Understand</div>
      <p class="how-step-desc">Read the interactive forest plot to see what the combined evidence shows. Not sure how? See our <a href="/eli5">beginner guide</a>.</p>
    </div>
  </div>
</div>
