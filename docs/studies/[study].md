---
index: false
---

```js
import * as duckdb from 'npm:@duckdb/duckdb-wasm'
import { DuckDBClient } from 'npm:@observablehq/duckdb'
import * as d3 from 'npm:d3'
import { sliderBottom } from 'npm:d3-simple-slider'
import { doubleRange } from '../createDoubleRange.js'

import { isArrayNumeric, getMValue,arrayToObjectOfArrays } from '../utils.js'
import { drawGraph } from "../fplot.js"
```

<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Martian+Mono:wght@100..800&family=Space+Grotesk:wght@300..700&display=swap" rel="stylesheet">
<link rel="stylesheet" href="../styles/doubleRange.css">
<link rel="stylesheet" href="../styles/styles.css">

<style>
  @media (max-width: 600px) {
  .mobile-block-range {
    display: block !important;
    width: 100% !important;
    margin-bottom: 1em;
  }
  .double-range {
    display: block !important;
    width: 100% !important;
  }
}

#chartArea {
  min-height: 0 !important;
  max-height: none !important;
  height: auto !important;
  overflow-x: auto;
  overflow-y: visible;
  width: 100%;
}
</style>

```js
// attach database to page i.e .read in actual data
const db = await DuckDBClient.of({
  axfors2021: FileAttachment(`../data/datasets/${observable.params.study}.csv`)
});

// get additional information about dta
let metaData = await FileAttachment(`../data/descriptions/${observable.params.study}.json`).json()

// load additional filter description data
let filterDescription = await FileAttachment(`../data/filters/${observable.params.study}.json`).json()

// transpose filter data
filterDescription = arrayToObjectOfArrays(filterDescription)
const tables = await db.sql`show tables`

// get table name
const tableName = [...tables][0]['name']

// load all data from table
const inputData = await db.query("select * FROM "+tableName)

```
```js

// define column names that should be skipped
const irrelevantColumns =['id','column00', 'acronym','yi','vi'] 

// get all column Names
const columnNames = await db.sql`select column_name from INFORMATION_SCHEMA.COLUMNS where TABLE_NAME=${tableName}`

// vector to collect selectors
const selectors =  {}
const filterMeta = {}

// exclude irrelevant columns
let relevantColumns = [...columnNames].filter(item => !irrelevantColumns.includes(item.column_name));

// sort by filter_order from study description metadata (if available)
const filterOrder = metaData?.filter_order ?? [];
if (filterOrder.length > 0) {
  relevantColumns.sort((a, b) => {
    const idxA = filterOrder.indexOf(a.column_name);
    const idxB = filterOrder.indexOf(b.column_name);
    // columns in filterOrder come first, in their specified order
    // columns not in filterOrder go to the end, preserving original order
    if (idxA === -1 && idxB === -1) return 0;
    if (idxA === -1) return 1;
    if (idxB === -1) return -1;
    return idxA - idxB;
  });
}

// for each columnn in the dataset
const numCols =[...relevantColumns].length

// loop through columns to create selectors
for (let i = 0; i < numCols; i++) {
  // get column
  const thisColumn = [...relevantColumns][i].column_name

  // check if we want to display the column
  if (!irrelevantColumns.includes(thisColumn)){
  // SQL query to get distinct values present in this column
  const sqlColumnValues = 'SELECT DISTINCT ' + thisColumn + ' FROM ' + tableName
  const ColumnValues = await db.query(sqlColumnValues)

// collect distinct values present in the column
  let distinctValues = []
  let j = -1

  // get alternative label for filter description (if available)
  let index = filterDescription['id'].findIndex(element => element[0] === thisColumn)
  let displayLabel = filterDescription['display_name'][index]
  let label = (displayLabel !== undefined && displayLabel.length >0) ? displayLabel : thisColumn;

// get potential information to display
let description = filterDescription['description'][index] === undefined ? "" : filterDescription['description'][index][0]

// get alternative values if they exist and create a dictionary based on them
const rawDisplayValues = filterDescription['display_values'][index]
let displayValues = Array.isArray(rawDisplayValues) ? [...rawDisplayValues] : []


// for each value in the column
while (++j < [...ColumnValues].length) {
    distinctValues[j] = [...ColumnValues][j][thisColumn]
  }
  let distinctValuesCopy = distinctValues


  // check whether all the values are numeric
  if (!isArrayNumeric(distinctValues)) {
  //let defaultValues = distinctValues
    // check if any of the inputs contains multiple inputs 
    for (let j = 0; j < distinctValues.length; j++){
      

  // check if some values are in fact multiple values separated by commas
      if (displayValues.length > 0){
      const displayValue = displayValues[j]
      if (typeof displayValue === "string" && displayValue.includes(',')){
         // split string
            let splitEntry = displayValue.split(',')
            // remove from distinctValues
            displayValues = displayValues.filter(item => item != displayValue)
            // add to options
            displayValues = [...displayValues, ...splitEntry]  

      }
         }
    }
// remove potential duplicates
distinctValues = [...new Set(distinctValues)];
  const obj = {};
  if (displayValues.length > 0){
    distinctValues.forEach((element, index2) => {
      const mappedLabel = displayValues[index2];
      if (mappedLabel !== undefined && mappedLabel !== null && mappedLabel !== "") {
        obj[element] = mappedLabel;
      }
    });
  }

// Compute counts for each check-box filter value
let valueCounts = {};
const isNumeric = isArrayNumeric(distinctValues);

for (let val of distinctValues) {
  let countQuery;
  if (isNumeric) {
    //countQuery = `SELECT * FROM ${tableName}`;
    countQuery = `SELECT id FROM ${tableName} WHERE ${thisColumn} = ${val}`;

  } else {
    // Escape single quotes in val
    let safeVal = String(val).replace(/'/g, "''");
    countQuery = `SELECT id FROM ${tableName} WHERE LOWER(${thisColumn}) = LOWER('${safeVal}')`;

  }
  let result = await db.query(countQuery);

  // Get array of unique IDs from result
  const resultArray = result.toArray();
  let count = resultArray ? new Set(resultArray.map(r => r.id)).size : 0;

  valueCounts[val] = count;
}

// set default value to all checkboxes checked
// except if  we have some values that were in fact two values separated by a comma
// then set the default option to empty 
let defaultValues = distinctValues
if (distinctValuesCopy.some(value => typeof value === "string" && value.includes(','))){
  defaultValues = []
}

// create checkbox selector
const myInput = Inputs.checkbox(distinctValues, { 
  format: (name) => {
    let labelText = Object.keys(obj).length > 0 ? obj[name] : name;
    let count = valueCounts[name] !== undefined ? ` (${valueCounts[name]})` : '';
    return labelText + count;
  },
  label: description.length > 0 ? html`
    <span  data-text="${description}"class="ttip" >${label}</span>` : label,
  unique: true,
  value: defaultValues
})
  filterMeta[thisColumn] = { type: "categorical", values: distinctValues, defaultValues }
// save selector in list of selectors
  selectors[thisColumn] = myInput
// else i.e. if data is numeric -> create range slider
} else {
  // get max value
  let maxVal = getMValue(distinctValues, 'Max')
  let minVal = getMValue(distinctValues, 'Min')

  const diff = maxVal - minVal

  // define step sizes for range slider -> this is a pretty random heuristic
  let step = 1
  if (diff < 1) {
    minVal = Math.floor(minVal * 1000) / 1000
    maxVal = Math.ceil(maxVal * 1000) / 1000

    step = 0.001
  } else if (diff < 10) {
    minVal = Math.floor(minVal * 10) / 10
    maxVal = Math.ceil(maxVal * 10) / 10
    step = 0.1
  } else {
    minVal = Math.floor(minVal)
    maxVal = Math.ceil(maxVal)
  }

  // create double range slider with live count update
  const myInput = doubleRange([minVal, maxVal], {
    label: description.length > 0 ? html`
      <span class="mobile-block-range" style="position:relative">
        <span data-text="${description}" class="ttip">${label}</span>
      </span>` : label,
    step: step,
    liveCountCallback: async (range) => {
      const res = await db.query('SELECT COUNT(*) as n FROM ' + tableName + ' WHERE ' + thisColumn + ' >= ' + range[0] + ' AND ' + thisColumn + ' <= ' + range[1]);
      const count = res.batches[0].data.children[0].values[0] ?? 0;
      myInput.updateCount(count);
    }
  })
  filterMeta[thisColumn] = { type: "range", min: minVal, max: maxVal, defaultValues: [minVal, maxVal] }
  // add to list of selectors
  selectors[thisColumn] = myInput

      // No initial trigger here anymore
}

}
}

// After creating all selectors, trigger their initial counts and set up tooltips
setTimeout(() => {
  // Initial counts for range sliders
  Promise.all(Object.keys(selectors).map(async (columnName) => {
    const selector = selectors[columnName];
    if (selector.updateCount && selector.value) {
      const initialRange = selector.value;
      const res = await db.query(`SELECT COUNT(*) as n FROM ${tableName} WHERE ${columnName} >= ${initialRange[0]} AND ${columnName} <= ${initialRange[1]}`);
      const count = res.batches[0].data.children[0].values[0] ?? 0;
      selector.updateCount(count);
    }
  }));

  const activeTooltips = new Map();
  const prefersHover = window.matchMedia('(hover: hover)').matches;

  const placeTooltip = (tooltip, trigger, event) => {
    if (event && typeof event.pageX === 'number' && typeof event.pageY === 'number') {
      tooltip.style.left = `${event.pageX + 14}px`;
      tooltip.style.top = `${event.pageY + 14}px`;
      return;
    }

    const rect = trigger.getBoundingClientRect();
    const scrollX = window.scrollX || window.pageXOffset;
    const scrollY = window.scrollY || window.pageYOffset;
    const maxLeft = scrollX + window.innerWidth - 280;
    const left = Math.min(maxLeft, rect.left + scrollX);
    const top = rect.bottom + scrollY + 10;
    tooltip.style.left = `${Math.max(scrollX + 8, left)}px`;
    tooltip.style.top = `${top}px`;
  };

  const hideTooltip = (trigger) => {
    const tooltip = activeTooltips.get(trigger);
    if (!tooltip) return;
    tooltip.remove();
    activeTooltips.delete(trigger);
    trigger.setAttribute('aria-expanded', 'false');
  };

  const hideAllTooltips = (exceptTrigger = null) => {
    for (const trigger of Array.from(activeTooltips.keys())) {
      if (trigger !== exceptTrigger) hideTooltip(trigger);
    }
  };

  const showTooltip = (trigger, event = null) => {
    const text = trigger.getAttribute('data-text');
    if (!text) return;
    hideAllTooltips(trigger);
    let tooltip = activeTooltips.get(trigger);
    if (!tooltip) {
      tooltip = document.createElement('div');
      tooltip.className = 'dynamic-tooltip';
      tooltip.textContent = text;
      document.body.appendChild(tooltip);
      activeTooltips.set(trigger, tooltip);
    }
    trigger.setAttribute('aria-expanded', 'true');
    placeTooltip(tooltip, trigger, event);
  };

  const outsideClickHandler = (event) => {
    if (event.target.closest('.ttip') || event.target.closest('.dynamic-tooltip')) return;
    hideAllTooltips();
  };

  document.querySelectorAll('.ttip').forEach((tooltipTrigger, idx) => {
    if (!tooltipTrigger.id) tooltipTrigger.id = `ttip-${idx + 1}`;
    tooltipTrigger.setAttribute('tabindex', '0');
    tooltipTrigger.setAttribute('role', 'button');
    tooltipTrigger.setAttribute('aria-expanded', 'false');
    tooltipTrigger.setAttribute('aria-haspopup', 'dialog');

    tooltipTrigger.addEventListener('mouseenter', (event) => {
      if (!prefersHover) return;
      showTooltip(tooltipTrigger, event);
    });
    tooltipTrigger.addEventListener('mousemove', (event) => {
      const tooltip = activeTooltips.get(tooltipTrigger);
      if (!tooltip) return;
      placeTooltip(tooltip, tooltipTrigger, event);
    });
    tooltipTrigger.addEventListener('mouseleave', () => hideTooltip(tooltipTrigger));

    tooltipTrigger.addEventListener('focus', () => showTooltip(tooltipTrigger));
    tooltipTrigger.addEventListener('blur', () => hideTooltip(tooltipTrigger));

    tooltipTrigger.addEventListener('click', (event) => {
      if (prefersHover) return;
      if (activeTooltips.has(tooltipTrigger)) hideTooltip(tooltipTrigger);
      else showTooltip(tooltipTrigger);
    });

    tooltipTrigger.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        if (activeTooltips.has(tooltipTrigger)) hideTooltip(tooltipTrigger);
        else showTooltip(tooltipTrigger);
      } else if (event.key === 'Escape') {
        hideTooltip(tooltipTrigger);
      }
    });
  });

  document.addEventListener('click', outsideClickHandler);
  invalidation.then(() => {
    document.removeEventListener('click', outsideClickHandler);
    hideAllTooltips();
  });
}, 0);

```
```js
const overviewText = String(metaData?.details?.[0] ?? "");
const overviewPreviewLimit = 320;
const overviewPreview = overviewText.slice(0, overviewPreviewLimit);
const overviewRest = overviewText.slice(overviewPreviewLimit);
const overviewHasMore = overviewRest.trim().length > 0;
const studyAliases = {
  "dat.axfors2021": "Hydroxychloroquine",
  "dat.aloe2013": "Supervision Quality",
  "dat.molloy2014": "Conscientiousness & Medication adherence",
  "dat.bangertdrowns2004": "Writing-to-Learn Interventions"
};
const studyAlias = studyAliases[observable.params.study] ?? "";
```
<section class="description study-hero">
  <div class="study-hero-header">
    <span class="study-hero-image" aria-hidden="true"></span>
    <div class="study-hero-content">
      <!-- <span class="study-eyebrow">Meta-analysis</span> -->
      <h1 class="study-title" title="${metaData['title'][0]}">${metaData['title'][0]}</h1>
      <!-- ${studyAlias ? `<span class="visually-hidden">${studyAlias}</span>` : ""} -->
    </div>
  </div>
  <div class="main-content">
    <div class="summary-grid">
      <div class="summary-box">
        <h3 class="summary-heading">Summary</h3>
        <p class="summary-text">${metaData['description'][0]}</p>
        <div class="overview-block">
          <p class="details-text overview-text">
            <span class="overview-preview">${overviewPreview}</span><span class="overview-ellipsis"${overviewHasMore ? "" : " hidden"}>...</span><span class="overview-rest" hidden>${overviewRest}</span>
          </p>
        </div>
      </div>
      <div class="summary-box">
        <h3 class="summary-heading">Citation</h3>
        <p class="details-text citation-text">
          <span class="citation-body">${metaData['source'][0]}</span>
          <button
            class="citation-copy"
            type="button"
            aria-label="Copy citation"
            title="Copy citation"
          >
            <svg class="citation-copy-icon" viewBox="0 0 24 24" aria-hidden="true">
              <path d="M8 8h9a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1H8a1 1 0 0 1-1-1V9a1 1 0 0 1 1-1zm-2 4H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h9a1 1 0 0 1 1 1v1" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
            </svg>
            <span class="citation-copy-label">Copy citation</span>
          </button>
        </p>
      </div>
    </div>
  </div>
</section>

```js
// Apply fallback class immediately to avoid first-paint hero width shifts.
try { document.body.classList.add("study-hero-page"); } catch (e) { /* ignore */ }

const getStudyHeroRoot = () => [...document.querySelectorAll("section.description.study-hero")].at(-1);
let root = getStudyHeroRoot();

// Small fallback if the hero markup is not yet attached.
if (!root) {
  await new Promise(requestAnimationFrame);
  root = getStudyHeroRoot();
}

if (!root) {
  console.warn("study-hero root not found.");
} else {
  // ---------- HERO IMAGE: randomize per load ----------
  const heroHeader = root.querySelector(".study-hero-header");
  if (heroHeader) {
    const heroImages = [
      "../data/images/heros/hero1.webp",
      "../data/images/heros/hero2.webp",
      "../data/images/heros/hero3.webp"
    ];
    const heroPick = heroImages[Math.floor(Math.random() * heroImages.length)];
    heroHeader.style.setProperty("--ml-hero-image", `url("${heroPick}")`);
  }
  // ---------- OVERVIEW: populate + toggle ----------
  const block = root.querySelector(".overview-block");
  const previewEl = block?.querySelector(".overview-preview");
  const ellipsisEl = block?.querySelector(".overview-ellipsis");
  const restEl = block?.querySelector(".overview-rest");
  const textRow = block?.querySelector(".overview-text");

  if (!block || !previewEl || !restEl || !textRow) {
    console.warn("Overview elements missing (overview-block/preview/rest/text).");
  } else {
    // Inject the actual strings (because Markdown HTML doesn't evaluate ${...})
    previewEl.textContent = overviewPreview ?? "";
    restEl.textContent = overviewRest ?? "";

    // Set correct initial visibility
    const hasMore = !!(overviewRest && overviewRest.trim().length > 0);
    if (hasMore) {
      ellipsisEl?.removeAttribute("hidden");
      restEl.setAttribute("hidden", "");
    } else {
      ellipsisEl?.setAttribute("hidden", "");
      restEl.setAttribute("hidden", "");
    }

    // Create button only if there's more text
    if (hasMore) {
      let btn = block.querySelector(".overview-toggle");
      if (!btn) {
        btn = document.createElement("button");
        btn.className = "overview-toggle";
        btn.type = "button";
        btn.textContent = "Read more";
        btn.setAttribute("aria-expanded", "false");
        textRow.appendChild(btn);
      }

      btn.addEventListener("click", () => {
        const isHidden = restEl.hasAttribute("hidden");
        if (isHidden) {
          restEl.removeAttribute("hidden");
          ellipsisEl?.setAttribute("hidden", "");
          btn.textContent = "Read less";
          btn.setAttribute("aria-expanded", "true");
        } else {
          restEl.setAttribute("hidden", "");
          ellipsisEl?.removeAttribute("hidden");
          btn.textContent = "Read more";
          btn.setAttribute("aria-expanded", "false");
        }
      });
    }
  }

  // ---------- CITATION COPY ----------
  const getCitationText = (button) => {
    const block = button.closest(".citation-text");
    if (!block) return "";
    const clone = block.cloneNode(true);
    clone.querySelector(".citation-copy")?.remove();
    return clone.textContent.replace(/\s+/g, " ").trim();
  };

  const highlightCitationDoi = () => {
    root.querySelectorAll(".citation-body").forEach((body) => {
      if (!body || body.dataset.doiProcessed) return;
      const text = body.textContent || "";
      const doiRegex = /(https?:\/\/(?:dx\.)?doi\.org\/\S+|10\.\d{4,9}\/[^\s]+)/i;
      const match = text.match(doiRegex);
      if (!match) return;
      const doi = match[0];
      const start = match.index ?? 0;
      const before = text.slice(0, start);
      const after = text.slice(start + doi.length);

      body.textContent = "";
      if (before) body.append(document.createTextNode(before));
      const doiSpan = document.createElement("span");
      doiSpan.className = "citation-doi";
      doiSpan.textContent = doi;
      body.append(doiSpan);
      if (after) body.append(document.createTextNode(after));
      body.dataset.doiProcessed = "true";
    });
  };

  highlightCitationDoi();

  root.querySelectorAll(".citation-copy").forEach((button) => {
    const citationText = getCitationText(button);
    if (citationText) button.dataset.citation = citationText;

    button.addEventListener("click", async () => {
      const text = button.dataset.citation || getCitationText(button);
      if (!text) return;

      const fallbackCopy = (t) => {
        const ta = document.createElement("textarea");
        ta.value = t;
        ta.setAttribute("readonly", "");
        ta.style.position = "fixed";
        ta.style.left = "-9999px";
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        document.body.removeChild(ta);
      };

      try {
        if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(text);
        else fallbackCopy(text);
        button.dataset.state = "copied";
        setTimeout(() => delete button.dataset.state, 1200);
      } catch {
        fallbackCopy(text);
      }
    });
  });
}

```

<section class="analysis">
<div class="main-content">
  <div class="summary-box filters-box">
    <h3 class="summary-heading">
      <button id="filters-toggle-button" class="filters-toggle" type="button" aria-expanded="true">Filters</button>
    </h3>
    <p class="filters-hint">Use the filters below to narrow which studies appear in the forest plot. Uncheck categories or adjust ranges to focus on the subset you care about.</p>

```js
// display filters
const options =  view(Inputs.form(selectors))
```

  </div>
</div>

```js
const conditions = [];
for (const key in options) {
  const value = options[key];
  if (value && value.length > 0) {
    if (Array.isArray(value) && typeof value[0] === 'number' && value.length === 2) {
      // Handle range sliders
      const meta = filterMeta[key];
      if (meta && meta.type === "range" && value[0] <= meta.min && value[1] >= meta.max) {
        continue;
      }
      conditions.push(`${key} >= ${value[0]} AND ${key} <= ${value[1]}`);
    } else {
      // Handle checkboxes
      const meta = filterMeta[key];
      if (meta && meta.type === "categorical") {
        const metaValues = meta.values.filter(v => v !== null && v !== undefined);
        if (value.length >= metaValues.length) {
          continue;
        }
      }
      const inValues = value.map(v => `LOWER('${String(v).replace(/'/g, "''")}')`).join(',');
      conditions.push(`LOWER(${key}) IN (${inValues})`);
    }
  }
}

let sqlFilter;
if (conditions.length > 0) {
  sqlFilter = `SELECT * FROM ${tableName} WHERE ${conditions.join(' AND ')}`;
} else {
  sqlFilter = `SELECT * FROM ${tableName}`;
}

// execute SQL query for filtering
const filteredData = (async () => {
  const data = await db.query(sqlFilter);
  return data.toArray(); // Convert to a standard JavaScript array
})()

// This logic is no longer needed
```


<div class="main-content" id="plotContent">

  <div class="summary-box forest-box">
    <h3 class="summary-heading">Forest plot</h3>

<p><span id="plot-count"></span></p>
<div class="plot-help-banner">
  New to forest plots? See our <a href="/eli5">beginner guide</a> or read the <a href="/methodology">detailed methodology</a>.
</div>

<div id="mobile-scroll-notice" style="display: none; color: #6c757d; margin-bottom: 1rem;">
  <p>Swipe sideways to see the full forest plot.</p>
</div>




<!-- Define Area to display chart -->

```js
// Make filters section collapsible; start collapsed on mobile
{
  const box = document.querySelector(".filters-box");
  const heading = box?.querySelector(".summary-heading");
  const toggleButton = box?.querySelector("#filters-toggle-button");
  if (box && heading && toggleButton) {
    const collapsibleItems = Array.from(box.children).filter((el) => el !== heading);

    const setExpanded = (expanded) => {
      toggleButton.setAttribute("aria-expanded", String(expanded));
      for (const el of collapsibleItems) {
        el.style.display = expanded ? "" : "none";
      }
    };

    if (!toggleButton.dataset.toggleBound) {
      toggleButton.dataset.toggleBound = "true";
      toggleButton.addEventListener("click", () => {
        const expanded = toggleButton.getAttribute("aria-expanded") === "true";
        setExpanded(!expanded);
      });
    }

    const mobileQuery = window.matchMedia("(max-width: 768px)");
    const applyResponsiveFiltersState = () => setExpanded(!mobileQuery.matches);

    applyResponsiveFiltersState();
    if (mobileQuery.addEventListener) {
      mobileQuery.addEventListener("change", applyResponsiveFiltersState);
    } else {
      mobileQuery.addListener(applyResponsiveFiltersState);
    }

    invalidation.then(() => {
      if (mobileQuery.addEventListener) {
        mobileQuery.removeEventListener("change", applyResponsiveFiltersState);
      } else {
        mobileQuery.removeListener(applyResponsiveFiltersState);
      }
    });
  }
}
```


<div id="no-data-message" style="display: none; color: #6c757d; margin: 2rem 0;">
  <p>No studies match the selected criteria.</p>
</div>

 <div id="chartArea"></div>

</div>
</div>




```js
const data = await filteredData;
const chartContainer = document.getElementById('chartArea');
const noDataMessage = document.getElementById('no-data-message');
const dataContainer = document.getElementById('data-section');
const scrollNotice = document.getElementById('mobile-scroll-notice');
const plotCountEl = document.getElementById('plot-count');
const fab = document.getElementById("filter-fab");

const totalCountResult = await db.query(`select count(*) as count from ${tableName}`);
const totalCount = totalCountResult.toArray()[0]?.count ?? (Array.isArray(data) ? data.length : 0);
const shownCount = Array.isArray(data) ? data.length : 0;
const isFull = shownCount >= totalCount;

if (plotCountEl) {
  plotCountEl.textContent = isFull
    ? `Showing all ${shownCount} studies.`
    : `Showing ${shownCount} out of ${totalCount} studies after applying your filters.`;
}

if (fab) {
  if (isFull) {
    fab.style.display = "none";
  } else {
    fab.style.display = "flex";
    fab.innerHTML = `
      <span class="filter-fab-count">Showing ${shownCount} out of ${totalCount} studies</span>
      <span class="filter-fab-action">Reset filters</span>
    `;
  }
  if (!fab.dataset.bound) {
    fab.dataset.bound = "true";
    fab.addEventListener("click", () => {
      Object.keys(selectors).forEach((key) => {
        const selector = selectors[key];
        const meta = filterMeta[key];
        if (!selector || !meta) return;
        if (meta.type === "range") {
          selector.value = meta.defaultValues ?? [meta.min, meta.max];
        } else if (meta.type === "categorical") {
          selector.value = meta.defaultValues ?? meta.values;
        }
        selector.dispatchEvent(new Event("input", { bubbles: true }));
      });
    });
  }
}

const updateScrollNotice = () => {
  if (!chartContainer || !scrollNotice) return;
  const isSmallScreen = window.matchMedia('(max-width: 768px)').matches;
  const isScrollable = chartContainer.scrollWidth > chartContainer.clientWidth + 8;
  scrollNotice.style.display = isSmallScreen && isScrollable ? 'block' : 'none';
};

const syncFabViewport = () => {
  if (!fab) return;
  const vv = window.visualViewport;
  if (!vv) {
    fab.style.opacity = "1";
    fab.style.pointerEvents = "auto";
    return;
  }
  const keyboardOpen = (window.innerHeight - vv.height) > 140;
  fab.style.opacity = keyboardOpen ? "0" : "1";
  fab.style.pointerEvents = keyboardOpen ? "none" : "auto";
};

let resizeObserver = null;
let renderFrame = null;
const schedulePlotRender = () => {
  if (renderFrame !== null) return;
  renderFrame = requestAnimationFrame(() => {
    renderFrame = null;
    if (!Array.isArray(data) || data.length === 0) return;
    drawGraph([...data]);
    updateScrollNotice();
  });
};

if (data && data.length > 0) {
  if (noDataMessage) noDataMessage.style.display = 'none';
  if (chartContainer) chartContainer.style.display = 'block';
  if (dataContainer) dataContainer.style.display = 'block';
  schedulePlotRender();

  if (chartContainer) {
    const resizeTarget = chartContainer.parentElement ?? chartContainer;
    resizeObserver = new ResizeObserver(() => schedulePlotRender());
    resizeObserver.observe(resizeTarget);
    chartContainer.addEventListener('scroll', updateScrollNotice, {passive: true});
  }

  window.addEventListener('resize', schedulePlotRender, {passive: true});
  window.addEventListener('orientationchange', schedulePlotRender, {passive: true});
  if (window.visualViewport) {
    window.visualViewport.addEventListener('resize', syncFabViewport, {passive: true});
    window.visualViewport.addEventListener('scroll', syncFabViewport, {passive: true});
  }
  syncFabViewport();
} else {
  if (chartContainer) chartContainer.style.display = 'none';
  if (dataContainer) dataContainer.style.display = 'none';
  if (noDataMessage) noDataMessage.style.display = 'block';
  if (scrollNotice) scrollNotice.style.display = 'none';
}

invalidation.then(() => {
  if (resizeObserver) resizeObserver.disconnect();
  if (chartContainer) chartContainer.removeEventListener('scroll', updateScrollNotice);
  window.removeEventListener('resize', schedulePlotRender);
  window.removeEventListener('orientationchange', schedulePlotRender);
  if (window.visualViewport) {
    window.visualViewport.removeEventListener('resize', syncFabViewport);
    window.visualViewport.removeEventListener('scroll', syncFabViewport);
  }
  if (renderFrame !== null) cancelAnimationFrame(renderFrame);
});
```

<div id="data-section">
<div class="main-content">
  <div class="summary-box forest-box">
    <h3 class="summary-heading">Data</h3>
    <p>Below is the plot data you can browse through. <span id="filtered-count"></span> You can also <button id="plot-data-download" class="plot-data-download" type="button">download it</button>.</p>

```js
const csv = d3.csvFormat(data);
const blob = new Blob([csv], {type: "text/csv;charset=utf-8"});
const url = URL.createObjectURL(blob);
invalidation.then(() => URL.revokeObjectURL(url));
const downloadButton = document.getElementById("plot-data-download");
if (downloadButton) {
  const fileName = `${observable.params.study}-plot-data.csv`;
  const onDownloadClick = () => {
    const a = document.createElement("a");
    a.href = url;
    a.download = fileName;
    document.body.appendChild(a);
    a.click();
    a.remove();
  };
  downloadButton.addEventListener("click", onDownloadClick);
  invalidation.then(() => downloadButton.removeEventListener("click", onDownloadClick));
}
const countResult = await db.query(`select count(*) as count from ${tableName}`);
const totalCount = countResult.toArray()[0]?.count ?? (Array.isArray(data) ? data.length : 0);
const shownCount = Array.isArray(data) ? data.length : 0;
const countEl = document.getElementById("filtered-count");
if (countEl) {
  const isFull = shownCount >= totalCount;
  countEl.textContent = isFull
    ? `This is the full data: ${shownCount} studies.`
    : `This is the filtered data after applying your selections: ${shownCount} of ${totalCount} studies.`;
}
```

<div id="mobile-data-actions" class="mobile-data-actions">
  <button id="mobile-data-toggle" class="mobile-data-toggle" type="button">Expand all</button>
</div>
<div id="mobile-data-cards" class="mobile-data-cards" aria-live="polite"></div>


```js

await new Promise(requestAnimationFrame);

const mobileCardsContainer = document.getElementById("mobile-data-cards");
const mobileActionsContainer = document.getElementById("mobile-data-actions");
const mobileToggleButton = document.getElementById("mobile-data-toggle");
const mobileDataQuery = window.matchMedia("(max-width: 768px)");

const formatCardValue = (value) => {
  if (value === null || value === undefined || value === "") return "-";
  if (typeof value === "number" && Number.isFinite(value)) return value.toLocaleString();
  return String(value);
};

const getMobileCards = () =>
  mobileCardsContainer ? Array.from(mobileCardsContainer.querySelectorAll(".mobile-data-card")) : [];

const updateMobileToggleState = () => {
  const cards = getMobileCards();
  const hasCards = cards.length > 0;
  const isMobile = mobileDataQuery.matches;
  if (mobileActionsContainer) mobileActionsContainer.style.display = isMobile && hasCards ? "flex" : "none";
  if (!mobileToggleButton) return;
  if (!hasCards) {
    mobileToggleButton.textContent = "Expand all";
    mobileToggleButton.disabled = true;
    return;
  }
  mobileToggleButton.disabled = !isMobile;
  const allOpen = cards.every((card) => card.open);
  mobileToggleButton.textContent = allOpen ? "Collapse all" : "Expand all";
};

const buildMobileDataCards = () => {
  if (!mobileCardsContainer) return;
  mobileCardsContainer.innerHTML = "";
  if (!Array.isArray(data) || data.length === 0) return;

  const keys = Object.keys(data[0]);
  const primaryKey = keys.includes("id") ? "id" : keys[0];
  const secondaryKey = keys.includes("yi") ? "yi" : keys.find((k) => k !== primaryKey);

  for (const row of data) {
    const card = document.createElement("details");
    card.className = "mobile-data-card";

    const summary = document.createElement("summary");
    const primaryText = formatCardValue(row[primaryKey]);
    const secondaryText = secondaryKey ? ` | ${secondaryKey}: ${formatCardValue(row[secondaryKey])}` : "";
    summary.textContent = `${primaryKey}: ${primaryText}${secondaryText}`;
    card.appendChild(summary);

    const list = document.createElement("div");
    list.className = "mobile-data-card-list";
    for (const key of keys) {
      const rowEl = document.createElement("div");
      rowEl.className = "mobile-data-card-row";
      const keyEl = document.createElement("span");
      keyEl.className = "mobile-data-card-key";
      keyEl.textContent = key;
      const valueEl = document.createElement("span");
      valueEl.className = "mobile-data-card-value";
      valueEl.textContent = formatCardValue(row[key]);
      rowEl.append(keyEl, valueEl);
      list.appendChild(rowEl);
    }
    card.appendChild(list);
    card.addEventListener("toggle", updateMobileToggleState);
    mobileCardsContainer.appendChild(card);
  }
  updateMobileToggleState();
};

const syncDataPresentation = () => {
  const mobile = mobileDataQuery.matches;
  const desktopTableContainer = document.getElementById("desktop-data-table");
  if (desktopTableContainer) desktopTableContainer.style.display = mobile ? "none" : "";
  if (mobileCardsContainer) mobileCardsContainer.style.display = mobile ? "grid" : "none";
  if (mobile) buildMobileDataCards();
  updateMobileToggleState();
};

const onMobileToggleClick = () => {
  const cards = getMobileCards();
  if (cards.length === 0) return;
  const shouldExpand = cards.some((card) => !card.open);
  for (const card of cards) card.open = shouldExpand;
  updateMobileToggleState();
};

if (mobileToggleButton) mobileToggleButton.addEventListener("click", onMobileToggleClick);

syncDataPresentation();
if (mobileDataQuery.addEventListener) {
  mobileDataQuery.addEventListener("change", syncDataPresentation);
} else {
  mobileDataQuery.addListener(syncDataPresentation);
}

invalidation.then(() => {
  if (mobileDataQuery.addEventListener) {
    mobileDataQuery.removeEventListener("change", syncDataPresentation);
  } else {
    mobileDataQuery.removeListener(syncDataPresentation);
  }
  if (mobileToggleButton) mobileToggleButton.removeEventListener("click", onMobileToggleClick);
});

```

```js
view((() => {
  const container = document.createElement("div");
  container.id = "desktop-data-table";
  container.className = "data-table-desktop";
  container.append(Inputs.table([...data]));
  syncDataPresentation();
  return container;
})())
```
</div>
</div></div>
</section>

<button id="filter-fab" class="filter-fab" type="button" aria-label="Reset filters"></button>
