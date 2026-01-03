```js
import * as duckdb from 'npm:@duckdb/duckdb-wasm'
import { DuckDBClient } from 'npm:@observablehq/duckdb'
import * as d3 from 'npm:d3'
import { sliderBottom } from 'npm:d3-simple-slider'
import { doubleRange } from '../createDoubleRange.js'

import { isArrayNumeric, getMValue,arrayToObjectOfArrays } from '../utils.js'
import { drawGraph } from "../fplot.js"
```

<link
  rel="stylesheet"
  href="https://stackpath.bootstrapcdn.com/bootstrap/4.1.3/css/bootstrap.min.css"
  integrity="sha384-MCw98/SFnGE8fJT3GXwEOngsV7Zt27NXFoaoApmYm81iuXoPkFOJwJ8ERdknLPMO"
  crossorigin="anonymous"
/>
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
  min-width: 400px;
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

// exclude irrelevant columns
const relevantColumns = [...columnNames].filter(item => !irrelevantColumns.includes(item.column_name));

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
let displayValues = filterDescription['display_values'][index]


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
      if ((displayValues !== undefined) && (displayValues.length > 0)){
      if (displayValues[j].includes(',')){
         // split string
            let splitEntry = displayValues[j].split(',')
            // remove from distinctValues
            displayValues = displayValues.filter(item => item != displayValues[j])
            // add to options
            displayValues = [...displayValues, ...splitEntry]  

      }
         }
    }
// remove potential duplicates
distinctValues = [...new Set(distinctValues)];
  let obj = [];
  if (distinctValues!== undefined && displayValues !== undefined){
    if (displayValues.length > 0){
    distinctValues.forEach((element, index2) => {
   // if(element !== undefined && filterDescription['display_values'][index] !== undefined){
    obj[element] = displayValues[index2]
   // }
  })
  }
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
if (distinctValuesCopy.some(value => value.includes(','))){
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

  // Global tooltip handler
  document.querySelectorAll('.ttip').forEach(tooltipTrigger => {
    tooltipTrigger.addEventListener('mouseenter', (event) => {
      const text = event.target.getAttribute('data-text');
      if (text) {
        const tooltip = document.createElement('div');
        tooltip.className = 'dynamic-tooltip';
        tooltip.textContent = text;
        document.body.appendChild(tooltip);

        const onMouseMove = (e) => {
          tooltip.style.left = `${e.pageX + 15}px`;
          tooltip.style.top = `${e.pageY + 15}px`;
        };

        document.addEventListener('mousemove', onMouseMove);

        event.target.addEventListener('mouseleave', () => {
          document.removeEventListener('mousemove', onMouseMove);
          tooltip.remove();
        }, { once: true });
      }
    });
  });
}, 0);

```
<section class="description study-hero">
  <span class="study-eyebrow">Meta-analysis</span>
  <h1 class="study-title" title="${metaData['title'][0]}">${metaData['title'][0]}</h1>
  <div class="main-content">
    <div class="summary-box">
      <h3 class="summary-heading">Summary:</h3>
      <p class="summary-text">${metaData['description'][0]}</p>
      <details class="study-details study-overview">
        <summary>Overview</summary>
        <p class="details-text">${metaData['details'][0]}</p>
      </details>
      <details class="study-details study-citation">
        <summary>Source and citation</summary>
        <p class="details-text source-text">${metaData['source'][0]}</p>
      </details>
    </div>
  </div>
</section>


<section class="analysis">
<h3>Filters</h3>

```js
// display filters
const options =  view(Inputs.form(selectors))
```


```js
const conditions = [];
for (const key in options) {
  const value = options[key];
  if (value && value.length > 0) {
    if (Array.isArray(value) && typeof value[0] === 'number' && value.length === 2) {
      // Handle range sliders
      conditions.push(`${key} >= ${value[0]} AND ${key} <= ${value[1]}`);
    } else {
      // Handle checkboxes
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


```js

```
<h3> Forest plot </h3>

<p>How does this forest plot work? See our <a href="/eli5" style="color: #0066cc">simple explanation</a> or <a href="/methodology" style="color: #0066cc">detailed methodology explanation.</a></p>


<!-- Define Area to display chart -->
<div id="mobile-scroll-notice" style="display: none; color: #6c757d; margin-bottom: 1rem;">
  <p>⟷ Note: The plot below can be scrolled horizontally.</p>
</div>

```js
// Show scroll notice only on mobile
if (window.innerWidth <= 600) {
  document.getElementById('mobile-scroll-notice').style.display = 'block';
}
```


<div id="no-data-message" style="display: none; color: #6c757d; margin: 2rem 0;">
  <p>No studies match the selected criteria.</p>
</div>

 <div id="chartArea"></div>





```js
const data = await filteredData;

// Only draw the graph if there is data to display
if (data && data.length > 0) {
  // Clear any 'no data' message
  const noDataMessage = document.getElementById('no-data-message');
  if (noDataMessage) noDataMessage.style.display = 'none';

  // Ensure the plot and data containers are visible
  const chartContainer = document.getElementById('chartArea');
  if (chartContainer) chartContainer.style.display = 'block';
  const dataContainer = document.getElementById('data-section');
  if (dataContainer) dataContainer.style.display = 'block';

  // Calculate dimensions and draw the graph
  let container = document.getElementById('chartArea');
  let width = container ? container.clientWidth : 600;
  let isMobile = window.innerWidth < 600;
  let height = isMobile
    ? width + width * data.length / 10   // much taller for mobile
    : width + width * data.length / 80;  // original for desktop
  if (container) {
    container.style.height = height + 'px';
  }
  drawGraph([...data]);
} else {
  // If there is no data, hide the plot and show the 'no data' message
  const chartContainer = document.getElementById('chartArea');
  if (chartContainer) chartContainer.style.display = 'none';
  const dataContainer = document.getElementById('data-section');
  if (dataContainer) dataContainer.style.display = 'none';
  const noDataMessage = document.getElementById('no-data-message');
  if (noDataMessage) noDataMessage.style.display = 'block';
}
```
<div id="data-section">
<h3>  Data </h3>

```js

view(Inputs.table([...data]))
```
</div>
</section>
