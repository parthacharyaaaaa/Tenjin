document.addEventListener('DOMContentLoaded', () => {
    const postID = window.location.pathname.split('/')[3];
    const SaveButton = document.getElementById('save');
    const postToast = document.getElementById('post-toast');

    window.dependencyReady?.then(async () => {
        if(!localStorage.getItem('login')){
            SaveButton.addEventListener('click', () => {
                alert("Please create an account to save this post");
                return;
            });
        }
        else{
            try{
                const response = await fetch(`/posts/${postID}/is-saved`, {
                    method : 'GET',
                    credentials : 'include'
                });

                if (!response.ok){
                    throw new Error('Failed to fetch post data')
                }

                let isSaved = await response.json();
                
                if(isSaved){
                    SaveButton.innerText = '💾 Saved'
                }

                SaveButton.addEventListener('click', async () => {
                    try{
                        const response = await fetch(`/posts/${postID}/${isSaved ? 'unsave' : 'save'}`, {
                            method : 'PATCH',
                            credentials:'include'
                        });

                        if(!response.ok){
                            throw new Error();
                        }

                        postToast.innerText = `Post ${isSaved ? '💾 Save' : '💾 Saved'}`
                        SaveButton.innerText = `${isSaved ? '💾 Save' : '💾 Saved'}`
                        postToast.classList.remove('hidden');
                        setTimeout(async () => {
                            postToast.classList.add('hidden');
                        }, 3000);

                        isSaved = !isSaved

                    }
                    catch (error){
                        console.error(error);
                    }
                })
            }
            catch(error){
                SaveButton.addEventListener('click', () => {
                    alert("Please login again to save this post");
                    return;
                });
                console.error(error);
                return;
            }
        }
    });
});