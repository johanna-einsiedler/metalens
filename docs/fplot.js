import * as d3 from 'npm:d3'

// function to draw the Graph
export const drawGraph = item => {
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
