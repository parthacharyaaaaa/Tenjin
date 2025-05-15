document.addEventListener('DOMContentLoaded', () => {
    const forumID = document.querySelector('meta[name="forumID"').getAttribute('value');
    const postBtn = document.getElementById('create-post-btn');
    const forumPromptWindow = document.getElementById('forum-modal');
    const forumPromptWindowBox = document.getElementById("forum-modal-box");

    const forumPosts = document.getElementById('posts-count');
    let updateCount = true;
    if (!forumPosts || forumPosts === undefined) {
        updateCount = false;
    }

    postsCount = forumPosts.innerText.trim().replace('.', '').replace(',', '');
    if (isNaN(postsCount)) {
        updateCount = false;
    }
    else {
        postsCount = parseInt(postsCount);
    }

    window.dependencyReady?.then(async () => {
        if (localStorage.getItem('login')) {
            postBtn.innerText = 'Create a post';
            postBtn.addEventListener('click', () => {
                forumPromptWindow.classList.remove('hidden');
                const outsideClickHandler = (event) => {
                    if (!forumPromptWindowBox.contains(event.target)) {
                        forumPromptWindow.classList.add('hidden');
                        document.removeEventListener('click', outsideClickHandler);
                    }
                };
                setTimeout(() => {
                    document.addEventListener('click', outsideClickHandler);
                }, 0.1);

                const submitButton = document.getElementById('forum-submit');
                submitButton.addEventListener('click', async () => {
                    try {
                        const title = document.getElementById('title').value;
                        if (!title || title === undefined || title.trim().length < 6) {
                            alert("Invalid post title");
                            return;
                        }

                        const desc = document.getElementById('desc')
                        if (!desc || desc === undefined || desc.value.trim() === '') {
                            alert("Please add a post body");
                            return false;
                        }

                        const body = desc.value.trim();


                        const response = await fetch('/posts', {
                            method: "POST",
                            credentials: 'include',
                            body: JSON.stringify({ title: title, body: body, forum: forumID }),
                            headers: {
                                'Content-Type': 'application/json'
                            }
                        });

                        if (!response.ok) {
                            throw new Error();
                        }

                        alert("Post created!");
                        forumPromptWindow.classList.add('hidden');
                        document.removeEventListener('click', outsideClickHandler);

                        if (updateCount) {
                            forumPosts.innerText = ++postsCount;
                        }

                    }
                    catch (error) {
                        console.error("Failed to create a post");
                        forumPromptWindow.classList.remove('hidden');
                        console.error(error)
                    }
                })
            });
        }
        else {
            postBtn.addEventListener('click', () => {
                window.location.href = '/login';
            })
        }
    });
});