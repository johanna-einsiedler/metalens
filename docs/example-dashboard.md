---
title: Mortality Outcomes with Hydroxychloroquine and Chloroquine in COVID-19 from an International Collaborative Meta-Analysis of Randomized Trials
toc: false
theme: [light, wide]
keywords: hydroxychloroquine, chloroquine, covid, covid-19, randomized, medical
---

<link rel="stylesheet" href="styles/styles.css">

<!-- Style options for graph -->
<style>
text {
  fill: #222;
}

.mark {
  fill: #333;
}

line.mark {
  stroke: #333;
  stroke-width: 2;
}

.heterogeneity-band {
	fill: #067;
	mix-blend-mode: multiply;
}
svg {
}

rect.overlay {
	stroke: black;
}

rect.selection {
	stroke: none;
  fill: steelblue;
  fill-opacity: 0.6;
}

#labelleft, #labelright {
	dominant-baseline: hanging;
  font-size: 12px;
}

#labelleft {
	text-anchor: end;
}

#labelright {
	text-anchor: start;
}

.ttip{
  position:relative; /*  making the .tooltip span a container for the tooltip text */
  border-bottom:1px dashed black; /* little indicater to indicate it's hoverable */

}
.ttip:before {
  content: attr(data-text); /* here's the magic */
  position:absolute;
    /* reset defaults */
  left:initial;
  margin:initial;
  /* vertically center */
  top:50%;
  transform:translateY(-50%);
  /* set new values */
  right:100%;
  margin-right:15px;

  /* basic styles */
  width:200px;
  padding:8px;
  border-radius:10px;
  background:white;
  color: black;
  text-align:right;

  display:none; /* hide by default */
}

.ttip:hover:before {
  display:block;
}
label {
  font-family: 'Times New Roman', Times, serif;
}

input[type="checkbox"]:checked {
  accent-color: #067;
}

</style>

```js
import * as duckdb from 'npm:@duckdb/duckdb-wasm'
import { DuckDBClient } from 'npm:@observablehq/duckdb'
import * as d3 from 'npm:d3'
import { sliderBottom } from 'npm:d3-simple-slider'
import { doubleRange } from './createDoubleRange.js'
```

<link
  rel="stylesheet"
  href="https://stackpath.bootstrapcdn.com/bootstrap/4.1.3/css/bootstrap.min.css"
  integrity="sha384-MCw98/SFnGE8fJT3GXwEOngsV7Zt27NXFoaoApmYm81iuXoPkFOJwJ8ERdknLPMO"
  crossorigin="anonymous"
/>
<link rel="stylesheet" href="doubleRange.css">


```js
// function to check if Array is numeric
function isArrayNumeric(arr) {
  return (
    Array.isArray(arr) &&
    arr.every(item => typeof item === 'number' && !isNaN(item))
  )
}

// function to get Max / Min value of array
function getMValue(arr, type) {
  if (type === 'Max') {
    return Array.isArray(arr) && arr.length > 0 ? Math.max(...arr) : null
  }
  if (type == 'Min') {
    return Array.isArray(arr) && arr.length > 0 ? Math.min(...arr) : null
  } else {
    return null
  }
}

// function to transpose array 
function arrayToObjectOfArrays(arrayOfObjects) {
    return arrayOfObjects.reduce((acc, obj) => {
        for (const [key, value] of Object.entries(obj)) {
            if (!acc[key]) {
                acc[key] = [];
            }
            acc[key].push(value);
        }
        return acc;
    }, {});
}
```
```js
// attach database to page -> will need to pass this as input, not possible to have dynamic arguments!
const db = await DuckDBClient.of({
  axfors2021: FileAttachment('./data/datasets/axfors2021.csv')
})


let metaData = await FileAttachment("./data/descriptions/dat.axfors2021.json").json()

// load additional filter description data
let filterDescription = await FileAttachment("./data/filters/dat.axfors2021.json").json()

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
// function to draw the Graph
const drawGraph = item => {
  // parse proxy item
  let data = JSON.parse(JSON.stringify(item))
console.log('datalen', data.length)
  //delete existing viz
  d3.select('#chartArea').selectAll('*').remove()

  // set dimensions
  let width = document.getElementById('chartArea').clientWidth

  let height = width+ width*data.length/60
  const margin = { top: 80, right: 20, bottom: 100, left: 400 }
  const innerWidth = width - margin.right - margin.left
  const innerHeight = height - margin.top - margin.bottom
  const innerLeftMargin = margin.left - margin.left * 0.05

  const statOffset = -20
  const titleOffset = -100
  const subtitleOffset = -70

  if (data && data.length > 0) {
    data.sort((a, b) => a.yi - b.yi)
  }
  // calculate lower and upper
  data.forEach(obj => {
    obj.ci_lower = obj.yi - Math.sqrt(obj.vi) // Calculate lower ci boundary
    obj.ci_upper = obj.yi + Math.sqrt(obj.vi) //calcualte upper ci boundary
    obj.n = 5 // for now set square size to  5 -> potentially make it adjust to sample size in the future
  })
  const xMax = d3.max(data, d => d.ci_upper)
  const xMin = d3.min(data, d => d.ci_lower)

  // check if adjusted odds ratios provided, if so take these
  // let i = 0
  //for (let i = 0; i < data.length; i++) {
  //if (data[i].a_point) {
  //console.log(data[i].a_point)
  //console.log(data[i].name)
  //data[i].point = data[i].a_point
  //data[i].lo = data[i].a_lo
  //data[i].hi = data[i].a_hi
  //data[i].lpoint = data[i].a_lpoint
  //data[i].se_lpoint = data[i].a_se_lpoint
  //}
  //}
  let svg = d3
    .select('#chartArea')
    .append('svg')
    .attr('width', width)
    .attr('height', height)
    //.attr("preserveAspectRatio", "xMinYMin meet")
    //.attr("viewBox", "0 0 300 300")
    //.classed("svg-content", true)
    .style('align', 'center')

  svg
    .append('g')
    .attr('transform', 'translate(' + margin.left + ',' + margin.top + ')')

  // https://www.meta-analysis.com/downloads/Intro_Models.pdf
  // calculate fixed effects meta estimate
  let i = 0
  let nom = 0
  let denom = 0
  let M = 0
  let WY = 0
  let WY2 = 0
  let W = 0
  let W2 = 0
  while (i < data.length) {
    //nom =nom + data[i].lpoint*(1/Math.pow(data[i].se_lpoint,2))
    //denom = denom + 1/Math.pow(data[i].se_lpoint,2)
    //WY = WY + data[i].lpoint * (1/Math.pow(data[i].se_lpoint,2))
    //WY2 = WY2 + Math.pow(data[i].lpoint,2) * (1/Math.pow(data[i].se_lpoint,2))
    //W = W+  (1/Math.pow(data[i].se_lpoint,2))
    //W2 =W2 +Math.pow((1/Math.pow(data[i].se_lpoint,2)),2)
    //i++;

    // calculate variance

    WY = WY + data[i].yi * (1 / data[i].vi) //multiply estimate of the study_names with inverse of it's variance i.e. weighting
    W = W + 1 / data[i].vi //add up the total weights
    WY2 = WY2 + Math.pow(data[i].yi, 2) * (1 / data[i].vi)
    //WY2 = WY2 + Math.pow(data[i].lpoint,2) * (1/Math.pow(data[i].se_lpoint,2))
    //W2 =W2 +Math.pow((1/Math.pow(data[i].se_lpoint,2)),2)
    //no_mask_risk.push(data[i].c/data[i].n2)
    i++
  }
  // fixed effects model
  let k = data.length //number of studies
  let FF = WY / W // calculate joint estiamte by dividing the sum of the weighted estimates by the sum of the weights
  let FF_VI = 1 / W // calculate the variance by taking the inverse of the sum of the weights

  Object.assign(data, {
    [k]: {
      id: 'Fixed effects model',
      yi: FF,
      //point: Math.exp(FF_LOR),
      vi: FF_VI,
      ci_lower: FF - Math.sqrt(FF_VI),
      ci_upper: FF + Math.sqrt(FF_VI)
    }
  })

  // calculate random effects meta estimate
  let df = k - 1

  let Q = WY2 - Math.pow(WY, 2) / W
  let C = W - W2 / W
  let T2 = (Q - df) / C

  nom = 0
  denom = 0
  i = 0
  while (i < data.length) {
    //nom = nom + data[i].lpoint * (1 / (Math.pow(data[i].se_lpoint, 2) + T2))
    //denom = denom + 1 / (Math.pow(data[i].se_lpoint, 2) + T2)
    //i++
    nom = nom + data[i].yi * (1 / (data[i].vi + T2))
    denom = denom + 1 / (data[i].vi + T2)
    i++
  }
  let RF = nom / denom
  let RF_VI = Math.sqrt(1 / denom)

  data = Object.assign(data, {
    [k + 1]: {
      id: 'Random effects model',
      yi: RF,
      vi: RF_VI,
      ci_lower: RF - Math.sqrt(RF_VI),
      ci_upper: RF + Math.sqrt(RF_VI)
    }
  })

  const xScale = d3
    .scaleLinear()
    .domain([
      xMin > 1 ? 1 - (xMax - xMin) : xMin,
      xMax < 1 ? 1 + (xMax - xMin) : xMax
      // 0,10
    ])
    .range([0, innerWidth])

  const yScale = d3
    .scalePoint()
    .domain(data.map(d => d.id))
    .range([0, innerHeight])
    .padding(1)

  const widthScale = d3
    .scaleLinear()
    .domain(d3.extent(data, d => d.n))
    .range([10, 17])

  const plotG = svg
    .attr('id', 'pG')
    .append('g')
    .attr('transform', `translate(${margin.left},${margin.top})`)

  //
  // title
  //
  // plotG
  // .append('text')
  //.attr('x', -innerLeftMargin)
  //.attr('y', titleOffset)
  //.attr('font-size', '1.5em')
  //.attr('font-weight', 'bold')
  //.text('Wearing Masks vs. Not Wearing Masks')

  //
  // subtitle
  //
  //plotG
  //.append('text')
  //.attr('x', -innerLeftMargin)
  //.attr('y', subtitleOffset)
  //.attr('font-size', '1.1em')
  //.text('Responder analysis - patients with controlled systolic blood pressure at 1 year')

  //plotG
  //.append('text')
  //.attr('x', -innerLeftMargin)
  //.attr('y', subtitleOffset)
  //.attr('dy', '1.2em')
  //.attr('font-size', '1.1em')
  //.html('(&le; 120 mmHg)')

  //
  // upper line
  //
  plotG
    .append('line')
    .attr('x1', -innerLeftMargin)
    .attr('x2', innerWidth)
    .attr('stroke', '#555')
    .attr('stroke-width', 2)

      // y axis line
  //
  plotG
    .append('line')
    .attr('transform', `translate(${xScale(0)},0)`)
    .attr('y2', innerHeight)
    .attr('stroke', '#555')
    .attr('stroke-width', 2)

  // heterogeneity bands
  //
  plotG
    .selectAll('rect')
    .data(data.filter((_, i) => i < data.length - 2))
    .join(enter => {
      return enter
        .append('rect')
        .classed('heterogeneity-band', true)
        .attr('id', d => d.id)
        .attr('x', d => xScale(d.ci_lower))
        .attr('width', d => xScale(d.ci_upper) - xScale(d.ci_lower))
        .attr('height', innerHeight)
        .attr('opacity', 0.01 + 1 / data.length)
        .attr('fill', '#067')
        .attr('mix-blend-mode', 'multiply')
    })

  const gSub = plotG
    .selectAll('g')
    .data(data.filter((_, i) => i < data.length - 2))
    .enter()
    .append('g')

      //

  //
  //
  // study rectangle
  //
  gSub
    .append('rect')
    .attr('x', d => xScale(d.yi) - widthScale(d.n) / 2)
    .attr('y', d => yScale(d.id) - widthScale(d.n) / 2)
    .attr('width', d => widthScale(d.n))
    .attr('height', d => widthScale(d.n))
    .attr('fill', '#333')
  //.classed('mark', true)

  // confint
  //
  gSub
    .append('line')
    //.classed('mark', true)
    .attr('transform', d => `translate(0,${yScale(d.id)})`)
    // .attr('x1', d => xScale(d.ci_lower))
    .attr('x1', d => xScale(d.ci_lower))
    .attr('x2', d => xScale(d.ci_upper))
    .attr('stroke', '#333')
    .attr('stroke-width', 2)
    .attr('fill', '#333')

  // stats area
  //
  const textG = gSub.append('g')

  textG
    .append('text')
    .text(d => d.id)
    .attr('x', -innerLeftMargin)
    .attr('y', d => yScale(d.id))
    .attr('dy', '0.32em')
    .attr('text-anchor', 'start')
    .attr('fill', '#222')
    //.attr('font-family', 'sans-serif')

  textG
    .append('text')
    .text(
      d =>
        `${d.yi.toFixed(2)} [ ${d.ci_lower.toFixed(2)} - ${d.ci_upper.toFixed(
          2
        )} ]`
    )
    .attr('x', -innerLeftMargin * 0.5)
    .attr('y', d => yScale(d.id))
    .attr('dy', '0.32em')

  //
  // ID label
  //
  plotG
    .append('text')
    .text('Study ID')
    .attr('x', -innerLeftMargin)
    .attr('y', statOffset)
    .attr('dy', '0.32em')
    .attr('text-anchor', 'start')

 // plotG
   // .append('text')
    //.attr('x', xScale(1))
    //.attr('y', statOffset)
    //.attr('dy', '0.32em')
    //.attr('text-anchor', 'end')
    //.attr('font-size', '0.9em')
    //.html('&#8592; Favors Mask Usage')

  //
  // stat label
  //
  const smDict = { OR: 'Odds ratio', RR: 'Risk ratio' }

  plotG
    .append('text')
    .text(` [95% CI]`)
    .attr('x', -innerLeftMargin * 0.5)
    .attr('y', statOffset)
    .attr('dy', '0.32em')
    .attr('text-anchor', 'start')

  //
  // x axis line
  //
  plotG
    .append('line')
    .attr('transform', `translate(0,${innerHeight})`)
    .attr('x1', -innerLeftMargin)
    .attr('x2', innerWidth)
    .attr('stroke', '#555')
    .attr('stroke-width', 2)

  //
  // x axis text
  //
  const xAxis = plotG.append('g')

  //const xTicks = [0.1, 0.5, 0.8, 1, 5, 10].filter(t => xScale(t) > 0)
  const min = Math.round(xScale.domain()[0]*100)/100
  const max = Math.round(xScale.domain()[1]*100)/100

  const xTicks = [min,0,max]

xAxis
    .selectAll('text')
    .data(xTicks)
    .enter()
    .append('text')
    .attr('x', d => xScale(d))
    .attr('y', innerHeight + 15)
    .attr('dy', '0.32em')
    .attr('text-anchor', 'middle')
    .attr('font-size', '0.8em')
    //.attr("transform", d =>`rotate(-65)`)
    .text(d => d)

  //
  // x axis ticks
  //
  xAxis
    .selectAll('line')
    .data(xTicks)
    .enter()
    .append('line')
    .attr('transform', d => `translate(${xScale(d)},${innerHeight - 5})`)
    .attr('y2', 10)
    .attr('stroke', '#555')
    .attr('stroke-width', '2')



  //
  // pooled
  //
  const pooledG = plotG
    .append('g')
    .attr('transform', `translate(0,${yScale.step() / 4})`)

  const pooledGSub = pooledG
    .selectAll('g')
    .data(data.filter((_, i) => i >= data.length - 2))
    .enter()
    .append('g')

  pooledGSub
    .append('polygon')
    //.classed('mark', true)
    .attr(
      'points',
      d => `
      ${xScale(d.ci_lower)}, ${yScale(d.id)}
      ${xScale(d.yi)}, ${yScale(d.id) + yScale.step() / 4}
      ${xScale(d.ci_upper)}, ${yScale(d.id)}
      ${xScale(d.yi)}, ${yScale(d.id) - yScale.step() / 4}
    `
    )
    .attr('fill', '#333')

  pooledGSub
    .append('text')
    .text(d => d.id)
    .attr('x', -innerLeftMargin)
    .attr('y', d => yScale(d.id))
    .attr('dy', '0.32em')
    .attr('text-anchor', 'start')

  pooledGSub
    .append('text')
    .text(
      d =>
        `${d.yi.toFixed(2)} [ ${d.ci_lower.toFixed(2)} - ${d.ci_upper.toFixed(
          2
        )} ]`
    )
    .attr('x', -innerLeftMargin * 0.5)
    .attr('y', d => yScale(d.id))
    .attr('dy', '0.32em')

  //let odds_estimate= document.getElementById('odds_estimate');
  //odds_estimate.innerHTML =data[data.length-2].point.toFixed(2)

  // let odds_estimate_perc= document.getElementById('odds_estimate_perc');
  //odds_estimate_perc.innerHTML =((1-data[data.length-2].point)*100).toFixed(0)

  //let odds_estimate_opposite= document.getElementById('odds_estimate_opposite');
  // odds_estimate_opposite.innerHTML = (1/data[data.length-2].point).toFixed(2)

  //let ci_lower= document.getElementById('ci_lower');
  //ci_lower.innerHTML = (data[data.length-2].lo).toFixed(2)

  //let ci_upper= document.getElementById('ci_upper');
  //ci_upper.innerHTML = (data[data.length-2].hi).toFixed(2)

  // hypermean
  plotG
    .append('text')
    .attr('x', -innerLeftMargin)
    .attr('y', innerHeight)
    .attr('dy', '2em')
    .attr('font-size', '1em')
    //.attr('font-weight', 'bold')
    .text(
      'Estimated Hypermean (Fixed Effects Model): ' +
        data[data.length - 2].yi.toFixed(2)
    )

  // hypermean
  plotG
    .append('text')
    .attr('x', -innerLeftMargin)
    .attr('y', innerHeight)
    .attr('dy', '3em')
    .attr('font-size', '1em')
    //.attr('font-weight', 'bold')
    .text(
      'Estimated Hypermean (Random Effects Model): ' +
        data[data.length - 1].yi.toFixed(2)
    )
}

drawGraph([...filteredData])
```
<h3>  Data </h3>

```js

view(Inputs.table([...filteredData]))
```
