document.addEventListener('DOMContentLoaded', async () => {
    const genreDropdown = document.getElementById('genres');

    try {
        const response = await fetch('/genres', {
            method: 'GET'
        });

        const data = await response.json();
        Object.entries(data).forEach(([genreName, genreId]) => {
            const genreOption = document.createElement('option');
            genreOption.value = genreId;
            genreOption.innerText = genreName;

            genreDropdown.appendChild(genreOption);
        });

        console.info(data)
    }
    catch (error) {
        console.error(error);
    }
})