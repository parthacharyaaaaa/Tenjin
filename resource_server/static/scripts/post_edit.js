document.addEventListener('DOMContentLoaded', () => {
    const postID = window.location.pathname.split('/')[3];
    const editButton = document.getElementById('edit-btn');
    const bodyDiv = document.getElementById('body');
    const titleDiv = document.getElementById('title');
    const postPromptWindow = document.getElementById('epost-modal');
    const postPromptWindowBox = document.getElementById("epost-modal-box");
    const submitButton = document.getElementById('edit-submit');

    const editBodyField = document.getElementById('edesc');
    const editTitleField = document.getElementById('etitle');
    window.dependencyReady?.then(() => {
        // Delete button is dynamically rendered anyways, no need to check localStorage for login key
        if (editButton && editButton !== undefined) {
            editButton.addEventListener('click', async () => {
                postPromptWindow.classList.remove('hidden');
                const outsideClickHandler = (event) => {
                    if (!postPromptWindowBox.contains(event.target)) {
                        postPromptWindow.classList.add('hidden');
                        document.removeEventListener('click', outsideClickHandler);
                    }
                };
                setTimeout(() => {
                    document.addEventListener('click', outsideClickHandler);
                }, 0.1);

                submitButton.addEventListener('click', async () => {
                    let newTitle = editBodyField.value.trim();
                    if(newTitle.length < 8){
                        alert('Title too short');
                        return;
                    }

                    let newDesc = editTitleField.value.trim();
                    try {
                        const response = await fetch(`/posts/${postID}?redirect=1`, {
                            method: 'PATCH',
                            credentials: 'include',
                            body:JSON.stringify({title : newTitle, body : newDesc}),
                            headers : {
                                'Content-Type' : 'application/json'
                            }
                        });

                        if (!response.ok) {
                            throw new Error('Failed to edit post, try again later');
                        }

                        const data = await response.json()
                        if ('title' in data) {
                            titleDiv.innerText = data.title;
                        }
                        if ('body_text' in data) {
                            bodyDiv.innerText = data.body_text;
                        }

                        postPromptWindow.classList.add('hidden');
                        document.removeEventListener('click', outsideClickHandler);
                    }
                    catch (error) {
                        console.error(error)
                        postPromptWindow.classList.add('hidden');
                        document.removeEventListener('click', outsideClickHandler);
                    }
                });
            });
        }
    });
});