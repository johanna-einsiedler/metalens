// function to check if Array is numeric
export function isArrayNumeric(arr) {
  return (
    Array.isArray(arr) &&
    arr.every(item => item === null || typeof item === 'number')
  );
}

// function to get Max / Min value of array
export function getMValue(arr, type) {
  if (!Array.isArray(arr) || arr.length === 0) return null;

  // Filter out NaN values
  const filteredArr = arr.filter(item => typeof item === 'number' && !isNaN(item));

  if (filteredArr.length === 0) return null; // Return null if all items are NaN

  if (type === 'Max') {
    return Math.max(...filteredArr);
  }
  
  if (type === 'Min') {
    return Math.min(...filteredArr);
  }
  
  return null; // Return null for invalid type
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