document.addEventListener('DOMContentLoaded', async () => {
    let dbCursor = 0;
    const forumContainer = document.getElementById('forum-container');
    let exhausted = false;
    const animeID = window.location.pathname.split("/")[3];
    
    async function fetchPosts() {
        try{
            const response = await fetch(`/animes/${animeID}/forums?cursor=${dbCursor}`, {
                method : "GET"
            });

            const data = await response.json();
            if(data.length === 0){
                exhausted = true;
            }

            if(dbCursor === 0){
                forumContainer.innerHTML = '';
            }

            dbCursor = data.cursor;

            data.forums.forEach(f => {
                const forum = document.createElement('div');
                forum.classList.add('forum-post');

                const forumTitle = f.name;
                const forumTitleElement = document.createElement('h3');
                forumTitleElement.innerText = forumTitle ? forumTitle : "Failed to load this forum's name"

                const metaDiv = document.createElement('div');
                metaDiv.classList.add('forum-meta');

                const posts = document.createElement('div');
                posts.innerText = `Posts: ${f.posts}`;

                const subs = document.createElement('div');
                subs.innerText = `Subscribers: ${f.subscribers}`;

                const admins = document.createElement('div');
                admins.innerText = `Admins: ${f.admin_count}`;

                const epoch = document.createElement('div');
                epoch.innerText = `Created: ${f.epoch.split(',')[0]}`;

                metaDiv.appendChild(posts)
                metaDiv.appendChild(subs)
                metaDiv.appendChild(admins)
                metaDiv.appendChild(epoch)

                const desc = document.createElement('p');
                desc.innerText = f.description;

                forum.appendChild(forumTitleElement);
                forum.appendChild(metaDiv);
                forum.appendChild(desc);

                forum.addEventListener('click', () => {
                    window.location.href = `/view/forum/${f.name}`
                })

                forumContainer.appendChild(forum);
            })

        }
        catch (error){
            console.error(`Failed to fetch animes. Cursor: ${dbCursor}.\nError Details: ${error}`);
        }
        
    }

    async function onScroll() {
        const nearBottom = window.innerHeight + window.scrollY >= document.body.offsetHeight - 400;
        if (nearBottom) fetchPosts();
    }

    window.addEventListener('scroll', async () => {
        if(!exhausted){
            onScroll();
        }
    })
    fetchPosts();
})