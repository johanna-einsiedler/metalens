// function to check if Array is numeric
export function isArrayNumeric(arr) {
  return (
    Array.isArray(arr) &&
    arr.every(item => typeof item === 'number' && !isNaN(item))
  )
}

// function to get Max / Min value of array
export function getMValue(arr, type) {
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
export function arrayToObjectOfArrays(arrayOfObjects) {
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