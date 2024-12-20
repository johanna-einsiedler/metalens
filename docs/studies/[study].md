



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
// console.log(thisColumn)
// console.log('display', displayValues)

// for each value in the column
while (++j < [...ColumnValues].length) {
    distinctValues[j] = [...ColumnValues][j][thisColumn]
  }
  let distinctValuesCopy = distinctValues
  // console.log('all distinct', distinctValues)
  // console.log('numeric?', isArrayNumeric(distinctValues))

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

// set default value to all checkboxes checked
// except if  we have some values that were in fact two values separated by a comma
// then set the default option to empty 
let defaultValues = distinctValues
if (distinctValuesCopy.some(value => value.includes(','))){
  defaultValues = []
}

// create checkbox selector
const myInput = Inputs.checkbox(distinctValues, { format: (name) =>  Object.keys(obj).length > 0 ? obj[name] :name, 
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
  // console.log('distinctmin',minVal)
  // get difference between min and max
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

  // create double range slider
  const myInput = doubleRange([minVal, maxVal], {
    label: description.length > 0 ? html`
    <span style="position:relative">
    <span  data-text="${description}"class="ttip" >${label}</span> </span>` : label,
    step: step
  })
  // add to list of selectors
  selectors[thisColumn] = myInput
}

}
}


```
<section class="description">
<h2> ${metaData['title'][0]} </h2>
<h4 class="studyDescription"> ${metaData['description'][0]}</h4>
<p> ${metaData['details'][0]}</p>
<p class="studySource"> <i> ${metaData['source'][0]} </i> </p>
</section>

<section class="analysis">
<h3>Filters</h3>

```js
// display filters
const options =  view(Inputs.form(selectors))
```


```js

// base for sql filter query
let sqlFilter = 'SELECT * FROM ' + tableName + ' WHERE '

// create SQL query for filtering the database according to selected options
for (let i = 0; i < numCols; i++) {
  // get relevant column to filter on
  let relevantColumn = [...relevantColumns][i].column_name    


  // if its not the first column add an AND
  if (!sqlFilter.endsWith(' ') && i < numCols ) {
    sqlFilter = sqlFilter + ' AND '
  }

// check if empty
//if (options[relevantColumn].length > 0){
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
// if not numeric and options undefined don't filter on this column
if (options[relevantColumn] !== undefined){
const defaultCheck = Array.from(selectors[relevantColumn]).every(radio => radio.defaultChecked)

// if we have some options selected
if (options[relevantColumn].length >0){

// if its not a column where the default is to have all unselected
     if (!defaultCheck){
  // add each selected option
  for (let k=0; k < options[relevantColumn].length; k++){

// syntax for first selected option
  if (k === 0){
    sqlFilter = sqlFilter + "LOWER(" + relevantColumn + ") LIKE LOWER('%" + options[relevantColumn][k] +"%')"
    }
// syntax for remaining selected optoins
    else{
    sqlFilter = sqlFilter  + " AND LOWER(" + relevantColumn + ") LIKE LOWER('%" + options[relevantColumn][k] +"%')"
  }
  } 
  } else {
// syntax if default is nothing selected
      sqlFilter =sqlFilter + " LOWER("  + relevantColumn + ") IN (LOWER('" + options[relevantColumn].join("'),LOWER('")+"'))"

 }

}
}
}
}

// execute SQL query for filtering
const filteredData = await db.query(sqlFilter)


// failed attempt at creating study counts preview
//const createTable = await db.query("DROP TABLE IF EXISTS test; CREATE TABLE test AS " + sqlFilter)
//const newTable = await db.query("select * FROM test")
//view([... newTable])
//studyCount = 20;


```


```js

```
<h3> Forest plot </h3>


<!-- Define Area to display chart -->
 <div id="chartArea"></div>



```js

drawGraph([...filteredData])
```
<h3>  Data </h3>

```js

view(Inputs.table([...filteredData]))
```
</section>