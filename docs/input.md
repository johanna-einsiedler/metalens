<link rel="stylesheet" href="styles/styles.css">

# Upload a meta-study dataset

Here you can upload file containing meta-study information and easily plot it as an interactive forest plot.
For this to work, the file needs to have the following format:
- You need one row per study
- You need a column named **id** that contains a unique identifier for each study
- You need a column named **yi** that contains the estimated effect size for each study
- You need a column named **vi** that contains the estimated effect size variance for each study

After uploading, you will be able to select which columns to include as filters in your plot.
Click **Continue** once you have selected all relevant variables.



```js
const file = view(Inputs.file({label: "CSV file", accept: ".csv", required: true}));


```
```js
// load data
const data = await file.csv({typed: true})

// create database
const db = await DuckDBClient.of({
  input: data
})
// load data from database
const inputData = await db.query("select * FROM input")

```

```js
// select columns to include in the analysis
let relCols = view(Inputs.checkbox(data.columns, {label: "Columns to include", value: data.columns}));


```

```js
let nonrelCols = data.columns.filter(element => !relCols.includes(element));

// filter data to relevant columns
let subset = db.query("alter table input drop column" + nonrelCols.join(", "))
```

```js
let idCol = null;
if (!relCols.includes('id')){
    idCol= view(Inputs.radio(relCols, {label: "ID Column"}));
}


let yiCol = null;

if (!relCols.includes('yi')){
    yiCol= view(Inputs.radio(relCols, {label: "Outcome column"}));

}

let viCol = null;
if (!relCols.includes('vi')){
viCol= view(Inputs.radio(relCols, {label: "Variance Column"}));
}
```

```js
let relColsNew = relCols
try { if (idCol !== null){
// rename id colum
let renameID = await db.query("alter table input rename column "+ idCol + " to id")


}} catch(error){}

try { if (yiCol !== null){
// rename yi colum

let renameyi = await db.query("alter table input rename column "+ yiCol + " to yi")

}} catch(error){}

try {
if (viCol !== null){
// rename id colum
let renamevi = await db.query("alter table input rename column "+ viCol + " to vi")

}} catch(error){}

if (idCol !== null && !relColsNew.includes('id')){
    relColsNew = relColsNew.filter(item => item !== idCol);
    relColsNew.push("id")
}

if (yiCol !== null && !relColsNew.includes('yi')){
    relColsNew = relColsNew.filter(item => item !== yiCol);
    relColsNew.push("yi")
}

if (viCol !== null && !relColsNew.includes('vi')){
    relColsNew = relColsNew.filter(item => item !== viCol);
    relColsNew.push("vi")
}

relColsNew = relColsNew.filter(item => item !=='');
let sub = await db.query("select "+relColsNew.filter(item=>item).join(", ")+" from input")


const confirm = await view(Inputs.button("OK", {label: "Continue?",value: 0}));
let input = await db.query("select * from input")

view(Inputs.table([...sub]))

```

```js

import * as duckdb from 'npm:@duckdb/duckdb-wasm'
import { DuckDBClient } from 'npm:@observablehq/duckdb'
import * as d3 from 'npm:d3'
import { doubleRange } from './createDoubleRange.js'

import { isArrayNumeric, getMValue,arrayToObjectOfArrays } from './utils.js'
import { drawGraph } from "./fplot.js"
```


<link rel="stylesheet" href="styles/doubleRange.css">


<style>
    .hiddenFirst{
    visibility: hidden;
    display: none;
    }

    
</style>


```js


let metaData = await FileAttachment("./data/descriptions/dat.axfors2021.json").json()

// load additional filter description data
let filterDescription = await FileAttachment("./data/filters/dat.axfors2021.json").json()

// transpose data
filterDescription = arrayToObjectOfArrays(filterDescription)
const tables = await db.sql`show tables`
const tableName = 'input'

// load all data from table
const inputData = await db.query("select * FROM "+tableName)


// define column names that should be skipped
const irrelevantColumns =['id','yi','vi'] 

// get all column Names
const columnNames = await db.sql`select column_name from INFORMATION_SCHEMA.COLUMNS where TABLE_NAME=${tableName}`

  // vector to collect selectors
const selectors =  {}
let relevantColumns = relColsNew
relevantColumns = [...relevantColumns].filter(item => !irrelevantColumns.includes(item));

// for each columnn in the dataset
const numCols =[...relevantColumns].length

// loop through columns to create selectors
for (let i = 0; i < numCols; i++) {
  // get column
  const thisColumn = relevantColumns[i]
  // SQL query to get distinct values present in this column
  const sqlColumnValues = 'SELECT DISTINCT ' + thisColumn + ' FROM ' + tableName
  const ColumnValues = await db.query(sqlColumnValues)

  // collect distinct values present in the column
  let distinctValues = []
  let j = -1

  // get alternative label for filter description (if available)
  let index = filterDescription['id'].findIndex(element => element[0] === thisColumn)
  let displayLabel = filterDescription['display_name'][index]
  let label = (displayLabel !== undefined && displayLabel.length >0) ? displayLabel : thisColumn



// get potential information to display
let description = filterDescription['description'][index] === undefined ? "" : filterDescription['description'][index][0]


// get alternative values if they exist and create a dictionary based on them
  let displayValues = filterDescription['display_values'][index]



while (++j < [...ColumnValues].length) {
    distinctValues[j] = [...ColumnValues][j][thisColumn]
  }
  let distinctValuesCopy = distinctValues
  // check whether all the values are numeric
  if (!isArrayNumeric(distinctValues)) {
    // check if any of the inputs contains multiple inputs
    for (let j = 0; j < distinctValues.length; j++){
      
      // check for multiple arguments separated by commas
      if (distinctValues[j].includes(',')){
            // split string
            let splitEntry = distinctValues[j].split(',')
            // remove from distinctValues
            distinctValues = distinctValues.filter(item => item != distinctValues[j])
            // add to options
            distinctValues = [...distinctValues, ...splitEntry]  

      }
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
  // remove spaces at end or beginning
  distinctValues = distinctValues.map(entry => entry.trim());
  let obj = [];
  if (distinctValues!== undefined && displayValues !== undefined){
    if (displayValues.length > 0){
    distinctValues.forEach((element, index2) => {
    obj[element] = displayValues[index2]
  })
  }
  }

let defaultValues = distinctValues
if (distinctValuesCopy.some(value => value.includes(','))){
  defaultValues = []
}

    const myInput = Inputs.checkbox(distinctValues, { format: (name) =>  Object.keys(obj).length > 0 ? obj[name] : name,
      label: description.length > 0 ? html`
      <span  data-text="${description}"class="ttip" >${label}</span>` : label,
      unique: true,
      value: defaultValues
    })

    selectors[thisColumn] = myInput
  } else {
    // get max value
    let maxVal = getMValue(distinctValues, 'Max')
    let minVal = getMValue(distinctValues, 'Min')

    // get difference between min and max
    const diff = maxVal - minVal

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
    const myInput = doubleRange([minVal, maxVal], {
      label: description.length > 0 ? html`
      <span style="position:relative">
      <span  data-text="${description}"class="ttip" >${label}</span> </span>` : label,
      step: step
    })

    selectors[thisColumn] = myInput
  }

}

```

```js
let options = null;
if (confirm >0){
 options =  view(Inputs.form(selectors))
}
```

```js
let sqlFilter = 'SELECT * FROM ' + tableName + ' WHERE '
if (confirm >0){
//create SQL query for filtering the database according to selected options
for (let i = 0; i < numCols; i++) {
  // get relevant column to filter on
  let relevantColumn = relColsNew[i]


  // if its not the first column add an AND
  if (!sqlFilter.endsWith(' ') && i < numCols ) {
    sqlFilter = sqlFilter + ' AND '
  }

  // check if numeric
  if ((isArrayNumeric(options[relevantColumn])) && (options[relevantColumn].length >0)) {

    sqlFilter =
      sqlFilter +
      relevantColumn +
      '>= ' +
      String(options[relevantColumn][0]) +
      ' AND ' +
      relevantColumn +
      '<=' +
      String(options[relevantColumn][1])
  } 
  else {
if (options[relevantColumn] !== undefined){
const defaultCheck = Array.from(selectors[relevantColumn]).every(radio => radio.defaultChecked)
if (options[relevantColumn].length >0){
  sqlFilter = sqlFilter + " ("
 for (let k=0; k < options[relevantColumn].length; k++){
  if (k === 0){
    sqlFilter = sqlFilter + "LOWER(" + relevantColumn + ") LIKE LOWER('%" + options[relevantColumn][k] +"%')"
  } else {   
      if (defaultCheck){

    sqlFilter = sqlFilter  + " OR LOWER(" + relevantColumn + ") LIKE LOWER('%" + options[relevantColumn][k] +"%')"
  } else {
   sqlFilter = sqlFilter  + " AND LOWER(" + relevantColumn + ") LIKE LOWER('%" + options[relevantColumn][k] +"%')"
  }
  }
 }
  sqlFilter =sqlFilter + " )"

}
}
}
}
}
let filteredData = []
if (confirm >0){
    filteredData = await db.query(sqlFilter)
}
```

<div id='plot' class='hiddenFirst'>
<h3> Forest plot</h3>

</div>

<!-- Define Area to display chart -->
 <div id="chartArea"></div>


```js

if (confirm >0){
drawGraph([...filteredData])
}
```
