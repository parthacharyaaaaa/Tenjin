document.addEventListener('DOMContentLoaded', () => {
    const forumID = document.querySelector('meta[name="forum-id"]').getAttribute('content');
    const postsContainer = document.getElementById('infinite-posts');
    const sortSelect = document.getElementById('sort');
    const timeframeSelect = document.getElementById('timeframe');

    let dbCursor = 0;
    let isFetching = false;
    let sortOption = 'latest';

    sortSelect.addEventListener('change', () => {
        sortOption = sortSelect.value;
        dbCursor = 0;
        postsContainer.innerHTML = '';
        fetchMorePosts();
    });

    timeframeSelect.addEventListener('change', () => {
        timeFrame = timeframeSelect.value;
        dbCursor = 0;
        postsContainer.innerHTML = '';
        fetchMorePosts();
    });

    async function fetchMorePosts() {
        if (isFetching) return;
        isFetching = true;

        try {
            const sortVal = sortOption === 'top' ? 1 : 0;
            const cursorParam = dbCursor ? encodeURIComponent(dbCursor) : 0;
            const response = await fetch(`/forums/${forumID}/posts?sort=${sortVal}&cursor=${cursorParam}`, {
                method: "GET"
            });

            if (!response.ok) {
                const errorText = await response.text();
                throw new Error(`Status: ${response.status}\n${errorText}`);
            }

            const data = await response.json();
            dbCursor = data.cursor;

            for (const post of data.posts) {
                const postDiv = document.createElement('div');
                postDiv.classList.add('highlighted-posts');
                postDiv.innerHTML = `
                    <h3>${post.title}</h3>
                    <p>${post.summary || post.body?.slice(0, 200) || "No summary"}</p>
                    <small>By: ${post.author} | ${post.epoch}</small>
                `;
                postsContainer.appendChild(postDiv);
            }

            if (!data.posts.length) {
                window.removeEventListener('scroll', onScroll);
            }
        } catch (err) {
            console.error("Fetch error:", err);
        } finally {
            isFetching = false;
        }
    }

    async function onScroll() {
        const nearBottom = window.innerHeight + window.scrollY >= document.body.offsetHeight - 500;
        if (nearBottom) fetchMorePosts();
    }

    // Start listening and fetch first batch
    document.body.addEventListener('scroll', onScroll);
    fetchMorePosts();
});
