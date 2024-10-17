---
theme: light
title: Example dashboard
toc: false
sql:
  masks: ./data/data_v2.json
  axfors2021: ./data/axfors2021.csv
---


<!-- https://www.thecoderashok.com/blog/double-range-slider-with-min-max-value -->
<!-- https://medium.com/@predragdavidovic10/native-dual-range-slider-html-css-javascript-91e778134816 -->

<style>
.sliders_control {
  position: relative;
  height: 0.2rem;
  border-radius: 20px;
  background-color: #efefef;
  border: 1px solid #b2b2b2;

}
.double {
  position: absolute;
  width: 100%;
  background: none;
  pointer-events: none;
  -webkit-appearance: none;
  -moz-appearance: none;
  transform: translateY(-50%);
}


.double {
  top: 50%;
  -webkit-appearance: none; 
  appearance: none;
  height: 7px;
  border-radius: 10px;
  width: 100%;
  position: absolute;
  background-color: #efefef;
  pointer-events: none;
}
.double::-webkit-slider-thumb {
  height: 15px;
  width: 15px;
  border-radius: 50%;
background-color: #4269d0;
  pointer-events: auto;
  -webkit-appearance: none;
  cursor: pointer;
  margin-bottom: 1px;
}
.double::-moz-range-thumb {
  height: 12px;
  width: 12px;
  border-radius: 50%;
  background-color: #4269d0;
  pointer-events: auto;
  -moz-appearance: none;
  cursor: pointer;
  margin-top: 30%;
}
#fromSlider {
  height: 0;
  z-index: 1;
}


</style>
```js

import {html} from "htl";
import {maybeWidth} from "./css.js";
import {checkValidity, preventDefault} from "./event.js";
import {formatTrim} from "./format.js";
import {identity} from "./identity.js";
import {maybeLabel} from "./label.js";

const epsilon = 1e-6;

function range(extent = [0, 1], inputRange, options) {
  return createRange({extent,inputRange, range: true}, options);
}

function createRange({
  extent: [min, max],
  inputRange,
  range
}, {
  format = formatTrim,
  transform,
  invert,
  label = "",
  value: initialValue,
  step,
  disabled,
  placeholder,
  validate = checkValidity,
  width
} = {}) {
  let value;
  if (typeof format !== "function") throw new TypeError("format is not a function");
  if (min == null || isNaN(min = +min)) min = -Infinity;
  if (max == null || isNaN(max = +max)) max = Infinity;
  if (min > max) [min, max] = [max, min], transform === undefined && (transform = negate);
  if (step !== undefined) step = +step;

//if (inputRange !== undefined) {

const number = html`<input style="width: 30px"  type=number id=numberLower min=${isFinite(min) ? min : null} max=${isFinite(max) ? max : null} step=${step == undefined ? "any" : step} name=numberLower required placeholder=${placeholder} oninput=${onnumber} disabled=${disabled}>`;

const numberUpper = html`<input style="width: 30px" type=number id=numberUpper min=${isFinite(min) ? min : null} max=${isFinite(max) ? max : null} step=${step == undefined ? "any" : step} name=numberUpper required placeholder=${placeholder} oninput=${onnumber} disabled=${disabled}>`;

  let irange; // untransformed range for coercion
  let range2;
  let irange2;
  let value2;
  if (range) {
    if (transform === undefined) transform = identity;
    if (typeof transform !== "function") throw new TypeError("transform is not a function");
    if (invert === undefined) invert = transform.invert === undefined ? solver(transform) : transform.invert;
    if (typeof invert !== "function") throw new TypeError("invert is not a function");
    let tmin = +transform(min), tmax = +transform(max);
    if (tmin > tmax) [tmin, tmax] = [tmax, tmin];

    range = html`<input class=double id=fromSlider type=range min=${isFinite(tmin) ? tmin : null} max=${isFinite(tmax) ? tmax : null} step=${step === undefined || (transform !== identity && transform !== negate) ? "any" : step} name=range oninput=${onrange} disabled=${disabled}>`;

    range2 = html`<input  class=double type=range min=${isFinite(tmin) ? tmin : null} max=${isFinite(tmax) ? tmax : null} step=${step === undefined || (transform !== identity && transform !== negate) ? "any" : step} name=range2 oninput=${onrange} disabled=${disabled}>`;


    irange = transform === identity ? range : html`<input type=range min=${min} max=${max} step=${step === undefined ? "any" : step} name=range disabled=${disabled}>`;


    irange2 = transform === identity ? range2 : html`<input type=range min=${min} max=${max} step=${step === undefined ? "any" : step} name=range disabled=${disabled}>`;
  } else {
    range = null;
    transform = invert = identity;
  }





   // const span = html`<span  class="range_track" id="range_track"></span>`
  const dRange = " double-range"
  const form = html`<form class=__ns__ style=${maybeWidth(width)}>
    ${maybeLabel(label, number)}<div class=__ns__-input${dRange}>
      ${number} - ${numberUpper}${range}${range2}
    </div>
  </form>`;
  form.addEventListener("submit", preventDefault);
  // If range, use an untransformed range to round to the nearest valid value.
  function coerce(v, slidertype) {
    if (slidertype === 'from'){
    if (!irange) return +v;
    v = Math.max(min, Math.min(max, v));
    if (!isFinite(v)) return v;
    irange.valueAsNumber = v;
    return irange.valueAsNumber;
  } else {
     v = Math.max(min, Math.min(max, v));
    if (!isFinite(v)) return v;
    irange2.valueAsNumber = v;
    return irange2.valueAsNumber;
  }
  }




  function onrange(event) {
 if (event.target.id ==='fromSlider'){
    const v = coerce(invert(range.valueAsNumber),'from');
    if (isFinite(v)) {
      number.valueAsNumber = Math.max(min, Math.min(max, v));
      if (validate(number)) {
        value = number.valueAsNumber;
        number.value = format(value);
        return;
      }
    }
    if (event) event.stopPropagation();
  }
  else {
        const v = coerce(invert(range2.valueAsNumber),'to');
if (isFinite(v)) {
      numberUpper.valueAsNumber = Math.max(min, Math.min(max, v));
      if (validate(numberUpper)) {
        value2 = numberUpper.valueAsNumber;
        numberUpper.value2 = format(value2);
        return;
      }
    }
     if (event) event.stopPropagation();
  }
    }
  
  function onnumber(event) {
    if (event.target.id ==='numberLower'){

    const v = coerce(number.valueAsNumber,'from');
    if (isFinite(v)) {
      if (range) range.valueAsNumber = transform(v);
      if (validate(number)) {
        value = v;
        return;
      }
    }
    if (event) event.stopPropagation();
  } else {
    const v = coerce(numberUpper.valueAsNumber,'to');
    if (isFinite(v)) {
      if (range2) range2.valueAsNumber = transform(v);
      if (validate(numberUpper)) {
        value2 = v;
        return;
      }
    }
    if (event) event.stopPropagation();
  }
  }


  Object.defineProperty(form, "value", {
    get() {
      return value;
    },
    set(v) {
    let v1;
    let v2;
    v1 = coerce(v[0],'from');
    v2 = coerce(v[1],'to');

      if (isFinite(v1) && isFinite(v2)) {
        number.valueAsNumber = v1;
        numberUpper.valueAsNumber = v2;

        if (range) range.valueAsNumber = transform(v1);
        if (range2) range2.valueAsNumber = transform(v2);

        if (validate(number) && validate(numberUpper)) {
          value = v;
          number.value = format(v1);
          numberUpper.value = format(v2);

        }
      }
    }
  });
  if (initialValue === undefined && irange) initialValue = [2,4]
  // irange.valueAsNumber/2; // (min + max) / 2
  //if (initialValue === undefined && irange2) initialValue = irange2.valueAsNumber; // (min + max) / 2

  if (initialValue !== undefined) form.value = initialValue; // invoke setter
  //if (initialValue !== undefined) form.value = initialValue; // invoke setter

  return form;
}

function negate(x) {
  return -x;
}

function square(x) {
  return x * x;
}

function solver(f) {
  if (f === identity || f === negate) return f;
  if (f === Math.sqrt) return square;
  if (f === Math.log) return Math.exp;
  if (f === Math.exp) return Math.log;
  return x => solve(f, x, x);
}

function solve(f, y, x) {
  let steps = 100, delta, f0, f1;
  x = x === undefined ? 0 : +x;
  y = +y;
  do {
    f0 = f(x);
    f1 = f(x + epsilon);
    if (f0 === f1) f1 = f0 + epsilon;
    x -= delta = (-1 * epsilon * (f0 - y)) / (f0 - f1);
  } while (steps-- > 0 && Math.abs(delta) > epsilon);
  return steps < 0 ? NaN : x;
}

```

```js
const test = range([0,10],{value:10})
view(test)
//const test3 = view(range([0,10],  [0,1], {value:50},))
const test2 = Inputs.range([0,100],{value:10})
//view(test2)
//const test3 = view(Inputs.range([0,100],{value:50}))
console.log(test.value)
console.log(test2)
//console.log(test2.value)
```
<!---
<div class="sliders_control" style='width:200px'>
        <input id="fromSlider" type="range" value="10" min="0" max="100"/>
        <input id="toSlider" type="range" value="40" min="0" max="100"/>
    </div>
--->