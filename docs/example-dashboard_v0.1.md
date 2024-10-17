---
theme: light
title: Example dashboard
toc: false
sql:
  masks: ./data/data_v2.json
  axfors2021: ./data/axfors2021.csv
---

# Rocket launches 🚀

<!-- Load and transform the data -->

```js
import * as duckdb from 'npm:@duckdb/duckdb-wasm'
import { DuckDBClient } from 'npm:@observablehq/duckdb'
import * as d3 from 'npm:d3'

//const data = await DuckDBClient.of({base: FileAttachment("/data/data_v2.json")});
//const c = await db.connect();
// Select a bundle based on browser checks
//const bundle = await duckdb.selectBundle(MANUAL_BUNDLES);
// Instantiate the asynchronus version of DuckDB-wasm
//const worker = new Worker(bundle.mainWorker!);
//const logger = new duckdb.ConsoleLogger();
//const db = new duckdb.AsyncDuckDB(logger, worker);
//await db.instantiate(bundle.mainModule, bundle.pthreadWorker);
//const launches = FileAttachment('data/launches.csv').csv({ typed: true })
//const studyDatabase = FileAttachment('data/study_database_test.csv').csv({
  //typed: true
//})

//const data = db2.sql`SELECT * FROM base`

const coerceRow = (d) => ({id: d.acronym,
                        //variable: d.variable,
                        //y: Number(d.y)});
                        //steps: Number(d.steps),
                        //fatigue: Number(d.fatigue),
                        yi: Number(d.yi),
                        vi: Number(d.vi),
                        patient_setting: d.patient_setting,
                        n: 5,
                        //ci_lower: Number(d.ci_lower),
                        //ci_upper: Number(d.ci_upper)
                        })
//const correlations  = FileAttachment("data/correlations.csv").csv({typed: true})
//const surveyData = FileAttachment("data/study_data.csv").csv({typed: true}).then((D) => D.map(coerceRow));
//const data = FileAttachment('/data/axfors2021.csv').csv({typed: true}).then((D) => D.map(coerceRow));
  // calculate lower and upper
  //data.forEach(obj => {
    //obj.ci_lower = obj.measure - Math.sqrt(obj.vi); // Calculate lower ci boundary
    //obj.ci_upper = obj.measure +Math.sqrt(obj.vi); //calcualte upper ci boundary
  //});

// define table name
const tableName = 'axfors2021';
```


 <div id="chartArea"></div>

```js
display([...columnNames])
//const search = view(Inputs.search(studyDatabase, {placeholder: "Search penguins…"}));
//const name = Generators.input(nameInput)

const mag = view(Inputs.range([0, 10], { label: 'Magnitude' }))
const db = await DuckDBClient.of({
  axfors2021: FileAttachment('/data/axfors2021.csv')
})

//console.log(Number(data[0].yi).toFixed(2))

//display(Inputs.table(test))
```

<!-- get column names -->
```sql id=columnNames
select column_name
from INFORMATION_SCHEMA.COLUMNS where TABLE_NAME=${tableName}
```


```js
// vector to collect selectors
const selectors = {}
for (let i = 3; i < 5; i++) {
// get column
const thisColumn = [...columnNames][i].column_name
// SQL query to get distinct values present in this column
const SQLString = 'SELECT DISTINCT ' + thisColumn + ' FROM axfors2021'
const queryResults = await db.query(SQLString)
// collect distinct values present in the column
let distinctValues = []
let j = -1
while (++j < [...queryResults].length) {
  distinctValues [j] = [...queryResults][j][thisColumn];
}
// create input selector
const myInput = Inputs.checkbox(distinctValues , {label: thisColumn});
//const myInput = view(Inputs.checkbox(['1','2','3'] , {label: 'test'}));
//selectors.push({[thisColumn]: myInput})
selectors[thisColumn] = myInput;
}

const options = view(Inputs.form(
  selectors
))


//const test  = view(Generators.input(myInput))
//console.log(Number([...waitPromise][10].yi))
//console.log([...columnNames][14].column_name)
//display(Inputs.table(queryResults[0]))

//const mag = view(Inputs.range([[...waitPromise][1].yi,[...waitPromise][10].yi), {label: "Magnitude"}));
//const mag = view(
  //Inputs.range([[...waitPromise][1].yi, [...waitPromise][10].yi], {
    //label: 'Magnitude'
  //})
//)
//display(col1)
```

Hello ${name || 'anonymous'}!
This is a ${options}

```sql id=inputData 
select * from axfors2021;
```



```sql id=col1Values
select distinct "id" from axfors2021;
```



<style>
text {
  fill: #222;
  font-family: sans-serif;
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

</style>
```js
display(options)
console.log(Object.keys(options).length)
display([...inputData])

function filterByValue(array, string){
  let newData = array;
  console.log(string)
  if (string.length>1){
  return array.filter( row => string.some(value => row[string].includes(value)))
  }
  if (string.length===1){
      return array.filter( row => row[string].includes(string))
  }
  else {
    return []
  }
}
let newData = filterByValue([...inputData], options.patient_setting)
for (let i = 3; i < 4; i++) {
//view([...inputData].filter((d) => d.patient_setting ==== 'Inpatient'))
let relevantColumn = [...columnNames][i].column_name;
const SQLString2 = "SELECT * FROM axfors2021 WHERE " + relevantColumn +" IN ('" + options.patient_setting.join("','") +"')"
console.log(SQLString2)
//const SQLString2 = "SELECT * FROM axfors2021 WHERE id ='NCT04353336'"
// WHERE patient_setting = "Inpatient"'

const queryResults2 = await db.query(SQLString2)
//view([...queryResults2])
}

const drawGraph = item => {

  // parse proxy item
  let data = JSON.parse(JSON.stringify(item))

  //delete existing viz
  d3.select('#chartArea').selectAll('*').remove()

  // set dimensions
  let width = document.getElementById('chartArea').clientWidth
  console.log('width', width)
  let height = width
  const margin = { top: 80, right: 20, bottom: 100, left: 400 }
  const innerWidth = width - margin.right - margin.left
  const innerHeight = height - margin.top - margin.bottom
  const innerLeftMargin = margin.left - margin.left*0.05

  const statOffset = -20
  const titleOffset = -100
  const subtitleOffset = -70


  if(data && data.length > 0) {
  data.sort((a, b) => a.yi- b.yi);
  }
  // calculate lower and upper
  data.forEach(obj => {
    obj.ci_lower = obj.yi - Math.sqrt(obj.vi); // Calculate lower ci boundary
    obj.ci_upper = obj.yi +Math.sqrt(obj.vi); //calcualte upper ci boundary
    obj.n = 5; // for now set square size to  5 -> potentially make it adjust to sample size in the future
  });
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
    let FF = WY/W // calculate joint estiamte by dividing the sum of the weighted estimates by the sum of the weights
    let FF_VI = 1/W; // calculate the variance by taking the inverse of the sum of the weights


    Object.assign(data,{[k]:{id: 'Fixed effects model',
                                          yi: FF,
                                          //point: Math.exp(FF_LOR),
                                          vi: FF_VI,
                                          ci_lower: FF - Math.sqrt(FF_VI),
                                          ci_upper: FF + Math.sqrt(FF_VI)}})

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
    nom =nom + data[i].yi*(1/(data[i].vi+T2))
    denom = denom + 1/(data[i].vi+T2)
    i++;
  }
  let RF= nom/denom;
  let RF_VI = Math.sqrt(1/denom);

  data = Object.assign(data,{[k+1]:{id: 'Random effects model',
                                        yi: RF,
                                        vi:RF_VI,
                                        ci_lower: RF - Math.sqrt(RF_VI),
                                        ci_upper: RF + Math.sqrt(RF_VI)
                                        
    
      }})

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
        .attr('fill','#067')
        .attr('mix-blend-mode','multiply')
    })


  const gSub = plotG
    .selectAll('g')
    .data(data.filter((_, i) => i < data.length - 2))
    .enter()
    .append('g')
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
    .attr('fill',  '#333')
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
    .attr('stroke-width',2)
    .attr('fill','#333')

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
    .attr('fill','#222')
    .attr('font-family', 'sans-serif')


  textG
    .append('text')
    .text(
      d => `${d.yi.toFixed(2)} [ ${d.ci_lower.toFixed(2)} - ${d.ci_upper.toFixed(2)} ]`
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

  plotG
    .append('text')
    .attr('x', xScale(1))
    .attr('y', statOffset)
    .attr('dy', '0.32em')
    .attr('text-anchor', 'end')
    .attr('font-size', '0.9em')
    .html('&#8592; Favors Mask Usage')

  //
  // stat label
  //
  const smDict = { OR: 'Odds ratio', RR: 'Risk ratio' }

  plotG
    .append('text')
    .text(` [95% CI]`)
    .attr('x', -innerLeftMargin * 0.6)
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

  const xTicks = [0.1, 0.5, 0.8, 1, 5, 10].filter(t => xScale(t) > 0)

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
  // y axis line
  //
  plotG
    .append('line')
    .attr('transform', `translate(${xScale(1)},0)`)
    .attr('y2', innerHeight)
    .attr('stroke', '#555')
    .attr('stroke-width', 2)

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
      d => `${d.yi.toFixed(2)} [ ${d.ci_lower.toFixed(2)} - ${d.ci_upper.toFixed(2)} ]`
    )
    .attr('x', -innerLeftMargin*0.5)
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
drawGraph([...inputData])
//drawGraph(await sql`SELECT * FROM masks LIMIT 2`)
//drawGraph([...queryResults2])
//drawGraph(sql`SELECT * FROM axfors2021`)
```

