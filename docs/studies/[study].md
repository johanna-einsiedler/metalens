

<link rel="stylesheet" href="../styles/styles.css">


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
<link rel="stylesheet" href="../doubleRange.css">


```js
// attach database to page -> will need to pass this as input, not possible to have dynamic arguments!
const db = await DuckDBClient.of({
  axfors2021: FileAttachment(`../data/datasets/${observable.params.study}.csv`)
})


let metaData = await FileAttachment(`../data/descriptions/${observable.params.study}.json`).json()

// load additional filter description data
let filterDescription = await FileAttachment(`../data/filters/${observable.params.study}.json`).json()

// transpose data
filterDescription = arrayToObjectOfArrays(filterDescription)
const tables = await db.sql`show tables`

// get table name
const tableName = [...tables][0]['name']

// load all data from table
const inputData = await db.query("select * FROM "+tableName)


// define column names that should be skipped
const irrelevantColumns =['id','column00', 'acronym','yi','vi'] 

// get all column Names
const columnNames = await db.sql`select column_name from INFORMATION_SCHEMA.COLUMNS where TABLE_NAME=${tableName}`

//const test = await FileAttachment('./data/filters/dat.axfors2021.json').json()

  // vector to collect selectors
const selectors =  {}

const relevantColumns = [...columnNames].filter(item => !irrelevantColumns.includes(item.column_name));
// for each columnn in the dataset
const numCols =[...relevantColumns].length

// loop through columns to create selectors
for (let i = 0; i < numCols; i++) {
  // get column
  const thisColumn = [...relevantColumns][i].column_name

  // check if we want to display the column
  //if (!irrelevantColumns.includes(thisColumn)){
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
  //let defaultValues = distinctValues
    // check if any of the inputs contains multiple inputs 
    for (let j = 0; j < distinctValues.length; j++){
      
      // check for multiple argumentes separated by commas
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

      // check if any inputs contaisn multuple inputs separated by and
      // if (distinctValues[j].includes(' and')){
      //       // split string
      //       let splitEntry = distinctValues[j].split('and')
      //       // remove from distinctValues
      //       distinctValues = distinctValues.filter(item => item != distinctValues[j])
      //       // add to options
      //       distinctValues = [...distinctValues, ...splitEntry]  
      // }
    
    }
  // remove spaces at end or beginning
  distinctValues = distinctValues.map(entry => entry.trim());
  // always capitalize first letter
 // distinctValues = distinctValues.map(entry => 
   // entry.charAt(0).toUpperCase() + entry.slice(1)
//);

// remove potential duplicates
//distinctValues = [...new Set(distinctValues)];
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

let defaultValues = distinctValues
if (distinctValuesCopy.some(value => value.includes(','))){
  defaultValues = []
}

    const myInput = Inputs.checkbox(distinctValues, { format: (name) =>  Object.keys(obj).length > 0 ? obj[name] : name,
      label: description.length > 0 ? html`
      <span  data-text="${description}"class="ttip" >${label}</span>` : label,
      unique: true,
      //<span title="this is displayed on hover"> ${label} </span>`,
      //, 
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

<h1> ${metaData['title'][0]} </h1>
<h4> ${metaData['description'][0]} </h4>
<p> ${metaData['details'][0]} </p>
<p> <i> ${metaData['source'][0]} </i> </p>

<h3> Filters </h3>

```js
const options =  view(Inputs.form(selectors))
```


```js

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

// let defaultOptions = [];
// for (let j=0; j< selectors[relevantColumn].length; j++){
//   console.log(selectors[relevantColumn][j].closest('label'))
//   const label = selectors[relevantColumn][j].closest('label').textContent.trim();
//   defaultOptions.push(label)

// }  
if (options[relevantColumn] !== undefined){
const defaultCheck = Array.from(selectors[relevantColumn]).every(radio => radio.defaultChecked)
  ///+ relevantColumn + " IN ('" + options[relevantColumn].join("','") +"')"
 
    //if filterTest.length == 

//  for (let k=0; k < options[relevantColumn].length; k++){
//   if (k === 0){
//     exclusiveFilter = exclusiveFilter  + "LOWER(" + relevantColumn + ") LIKE LOWER('%" + options[relevantColumn][k] +"%')"
//   } else { 
//      exclusiveFilter = exclusiveFilter  + " OR LOWER(" + relevantColumn + ") LIKE LOWER('%" + options[relevantColumn][k] +"%')"
//   }
//   }



// let exclusiveFilter = "SELECT * FROM " + tableName + " WHERE LOWER("  + relevantColumn + ") IN (LOWER('" + defaultOptions.join("'),LOWER('")+"'))"
//   const filterTest = await db.query(exclusiveFilter)
//   console.log('exclusive filter', [...filterTest].length)
//   console.log(exclusiveFilter)
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
// execute SQL query for filtering
const filteredData = await db.query(sqlFilter)


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
