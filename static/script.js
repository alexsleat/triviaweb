const join_btn = document.getElementById('join_btn');
const create_btn = document.getElementById('create_btn');


join_btn.addEventListener('click', () => {

  var sj_div = document.getElementById('start_join');
  var sc_div = document.getElementById('start_create');

  sc_div.style.display = 'none';

  if(sj_div.style.display == 'none')
    sj_div.style.display = 'block';
  else
    sj_div.style.display = 'none';

});

create_btn.addEventListener('click', () => {

  var sj_div = document.getElementById('start_join');
  var sc_div = document.getElementById('start_create');

  sj_div.style.display = 'none';
  
  if(sc_div.style.display == 'none')
    sc_div.style.display = 'block';
  else
    sc_div.style.display = 'none';

});
