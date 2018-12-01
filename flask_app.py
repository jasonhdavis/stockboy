#!venv/bin/python
# -*- coding: utf-8 -*-

import os
import time

from datetime import datetime, timedelta
from decimal import *
import locale
locale.setlocale(locale.LC_ALL, '')
from flask import Flask, url_for, redirect, render_template, request, abort, flash
from flask_mongoengine import MongoEngine
from flask_security import Security, \
    UserMixin, RoleMixin, login_required, current_user, datastore, MongoEngineUserDatastore
from flask_security.utils import encrypt_password
import flask_admin
from flask_admin.contrib import sqla
from flask_admin import helpers as admin_helpers
from flask_admin import BaseView, expose
from flask_bootstrap import Bootstrap
from flask_datepicker import datepicker
from flask_admin.contrib.pymongo import ModelView
from flask_wtf import Form, FlaskForm
from wtforms import StringField, PasswordField, TextField, SubmitField, validators, HiddenField
from flask_wtf.file import FileField, FileRequired
from werkzeug.utils import secure_filename
from werkzeug.datastructures import CombinedMultiDict
from flask_pymongo import PyMongo
from bson.objectid import ObjectId
import xlrd



# Create Fldask application
app = Flask(__name__)
app.config.from_pyfile('config.py')

Bootstrap(app)
datepicker(app)

mongo = PyMongo(app)
enginedb = MongoEngine(app)
#scheduler = BackgroundScheduler()
#atexit.register(lambda: scheduler.shutdown())
now = datetime.now()
today = datetime(now.year, now.month, now.day)

######################################
############# FUNCTIONS ##############
######################################

def StripTimezone(datestring) :
    output = str(datestring).split('.')
    output = output[0]
    return output

def DateDictBuilder(start,end):
    ## Build date range dictionary
    ## Documentation at: https://gist.github.com/jasonhdavis/73664baf24bdb595ffe0b66d01703c01
    date_dict={}
    labels = []
    date_dict.update({start.year:{start.month:{start.day: 0}}})
    delta_range = (end - start).days
    y_keys = [str(start.year)]
    m_keys = [str(start.month)+"-"+str(start.year)]

    for i in range(delta_range):
        iter_date = start+timedelta(days=i)
        year = iter_date.year
        month = iter_date.month
        day = iter_date.day
        labels.append(iter_date.strftime('%m-%d'))
        if str(year) in y_keys and str(month)+"-"+str(year) in m_keys :
            date_dict[year][month][day] = 0
        elif str(year) in y_keys :
            date_dict[year].update({month:{day:0}})
            m_keys.append(str(month)+"-"+str(year))
        else :
            date_dict.update({year:{month:{day:0}}})
            y_keys.append(str(year))
            m_keys.append(str(month)+"-"+str(year))

    return date_dict, labels

def DateFormHanlder(formvalue):
    if formvalue :
        daterange = formvalue.split(' - ')
        start = datetime.strptime(daterange[0],'%m/%d/%Y')
        end = datetime.strptime(daterange[1], '%m/%d/%Y')
        #Because time ends at midnight, add one extra day, which represents midnight
        end = end
    else :
        start = today - timedelta(days=30)
        end = today
    return start, end

def AliasDictBuilder(cursor):
    ## Alias Dictionary can be built based on context
    ## For example, a single sku or all Owner Alias, etc
    alias_dict = {}
    for alias in cursor :
        alias_dict[alias['Alias']] = alias['Product SKU']
    return alias_dict

def DiscountHandler():

    #if "Discount" in name :
        #continue

    ## Discount Handler ##

    #discount = 0
    #discount_percent = False

    #for item in l['items']:
    #    iter_sku = item['sku']
    #    if 'Discount' in item['name'] :
    #        discount = item['unitPrice']

    #    if iter_sku not in alias_list :
    #        continue
    #if discount != 0 :
    #    if discount < 0:
    #        discount = discount * -1
    #    gross = discount+order_total
    #    discount_percent = discount/gross

    #    item_discount = sales_total*discount_percent`
    pass

def ItemChartBuilder(cursor, date_dict, alias_dict, item_sku):
    shipped_to_amz = 0
    item_chart = []
    sku_list = []
    for order in cursor :
        # Sum Order Totals
        payment_date = order['paymentDate']

        # Count Amz Quantity
        ship_to = order['shipTo']['name']
        if ship_to.find('Amazon') >-1 or ship_to.find('Golden State FC') >-1:
            for item in order['items'] :
                sku = item['sku']
                if sku == item_sku or item_sku == 'All':
                    shipped_to_amz += item['quantity']
                # Do nothing with these duplicate quantities
            continue

        # Count item qty & sales
        # Join on product Alias
        for item in order['items']:
            row = []
            sku = item['sku']

            if sku in alias_dict :
                sku = alias_dict[sku]

            ## To be implemented ##

            if sku == "":
                continue

            name = item['name']
            qty = item['quantity']
            sales = item['unitPrice']*qty

            if sku in sku_list :
                sku_idx = sku_list.index(sku)
                item_chart[sku_idx][2] += sales
                item_chart[sku_idx][3] += qty
                if sku == item_sku or item_sku == 'All':
                    date_dict[payment_date.year][payment_date.month][payment_date.day]+= sales

            else :
                row.append(sku)
                row.append(name)
                row.append(sales)
                row.append(qty)
                sku_list.append(sku)
                item_chart.append(row)
                if sku == item_sku or item_sku == 'All' :
                    date_dict[payment_date.year][payment_date.month][payment_date.day]+= sales

    #Build chart value list
    values=[]

    for year in date_dict.values():
        for month in year.values():
          for day in month.values() :
              values.append(day)

    return item_chart, values, shipped_to_amz


class MongoRole(enginedb.Document, RoleMixin):
    name = enginedb.StringField(max_length=80, unique=True)
    description = enginedb.StringField(max_length=255)

class MongoUser(enginedb.Document, UserMixin):
    email = enginedb.StringField(max_length=255)
    password = enginedb.StringField(max_length=255)
    active = enginedb.BooleanField(default=True)
    confirmed_at = enginedb.DateTimeField()
    roles = enginedb.ListField(enginedb.ReferenceField(MongoRole), default=[])
    ss_key = enginedb.StringField(max_length=255)
    ss_secret = enginedb.StringField(max_length=255)
    ss_lastupdated = enginedb.DateTimeField()
    inventory_updated = enginedb.DateTimeField()
    alias_updated = enginedb.DateTimeField()


# Setup Flask-Security
#user_datastore = SQLAlchemyUserDatastore(db, User, Role)
#security = Security(app, user_datastore)
user_datastore = MongoEngineUserDatastore(enginedb, MongoUser, MongoRole)
security = Security(app, user_datastore)

# Create customized model view class
class MyModelView(sqla.ModelView):

    def is_accessible(self):
        if not current_user.is_active or not current_user.is_authenticated:
            return False

        if current_user.has_role('superuser'):
            return True

        return False

    def _handle_view(self, name, **kwargs):
        """
        Override builtin _handle_view in order to redirect users when a view is not accessible.
        """
        if not self.is_accessible():
            if current_user.is_authenticated:
                # permission denied
                abort(403)
            else:
                # login
                return redirect(url_for('security.login', next=request.url))


    can_edit = True
    edit_modal = True
    create_modal = True
    can_export = True
    can_view_details = True
    details_modal = True

class MongoUserView(MyModelView):
    column_editable_list = ['email', 'first_name', 'last_name']
    column_searchable_list = column_editable_list
    column_exclude_list = ['password']
    #form_excluded_columns = column_exclude_list
    column_details_exclude_list = column_exclude_list
    column_filters = column_editable_list

class InventoryView(BaseView):
    @expose('/',methods=('GET', 'POST'))
    def InventoryReport(self):
        return 'Build this'

class AmazonView(BaseView):
    @expose('/',methods=('GET', 'POST'))
    def Amazon(self):
        return 'Build this'

class BurnView(BaseView):
    @expose('/',methods=('GET', 'POST'))
    def BurnReport(self):
        return 'Build this'

class FileUploadForm(FlaskForm) :
    file = FileField(validators=[FileRequired()])
    submit = SubmitField('Upload')

class SSAPI(FlaskForm):
    user = HiddenField()
    key = TextField(validators.DataRequired())#current_user.ss_key)
    secret = PasswordField(validators.DataRequired())#current_user.ss_secret)
    submit = SubmitField('Submit')

class ProfileView(BaseView):
    @expose('/', methods=('GET','POST'))
    def UserProfile(self):
        apiform = SSAPI(prefix='apiform')
        inventoryform = FileUploadForm(prefix='inventoryform')
        aliasform = FileUploadForm(prefix='aliasform')
        nice_now = datetime.strftime(now,"%m-%d-%Y at %I:%M %p")

        if request.method == 'POST' and apiform.submit.data:
            flash('ShipStation API Key Updated - Data will begin showing shortly')
            userid = current_user.id
            current = mongo.db.mongo_user.find_one({"_id": ObjectId(userid)})
            current['ss_key'] = apiform.key.data
            current['ss_secret'] = apiform.secret.data
            mongo.db.mongo_user.update({"_id":ObjectId(userid)},{'$set':{'ss_key':apiform.key.data, 'ss_secret':apiform.secret.data}},upsert=True)
            #current_user.ss_key = apiform.key.data
            #current_user.ss_secret = apiform.secret.data
            return redirect(url_for('import.UserProfile'))

        if inventoryform.validate_on_submit() and inventoryform.submit.data:
            f = inventoryform.file.data
            filename = secure_filename(f.filename)
            save_path = os.path.join(app.root_path,'uploads', filename)
            f.save(save_path)
            wb = xlrd.open_workbook(save_path)
            ws = wb.sheet_by_index(0)
            if ws.cell_value(0,0) != 'ShipStation Stock Report' :
                flash('File does not match expected Inventory Stock Report format')
            else:
                col_len = len(ws.col(0))
                r = 8
                update_count = 0
                while r < col_len :
                    sku = str(ws.cell_value(r,1))
                    name = str(ws.cell_value(r,2).encode('utf8'))
                    if sku == "":
                        #Shipstation merges name column vertically
                        # Leaving  blank sku cells below
                        continue

                    row = {}
                    row['SKU'] = sku
                    row['Product Name'] = name
                    row['Last Cost'] = ws.cell_value(r,3)
                    row['Avg Cost'] = ws.cell_value(r,4)
                    row['Stock'] = ws.cell_value(r,6)
                    row['Allocated'] = ws.cell_value(r,7)
                    row['Available'] = ws.cell_value(r,8)
                    row['Reorder Threshold'] = ws.cell_value(r,9)
                    row['Owner'] = current_user.email
                    mongo.db.inventory.update({'SKU':str(sku)},row,upsert=True)
                    r+=1
                    update_count+=1
                flash('Imported '+ str(update_count) + ' Inventory Stock Values')
                mongo.db.mongo_user.update({'email':current_user.email},{'$set':{'inventory_updated':nice_now}})
            return redirect(url_for('import.UserProfile'))

        if aliasform.validate_on_submit() and aliasform.submit.data:
            f = aliasform.file.data
            filename = secure_filename(f.filename)
            save_path = os.path.join(app.root_path,'uploads', filename)
            f.save(save_path)
            wb = xlrd.open_workbook(save_path)
            ws = wb.sheet_by_index(0)
            col_len = len(ws.col(0))
            r = 2
            if ws.cell_value(0,2) != 'Store Name':
                flash('Uploaded File does not match expected Alias Report Format')
            else:
                while r < col_len :
                    row = {}
                    row['Product SKU'] = str(ws.cell_value(r,0))
                    row['Alias'] = str(ws.cell_value(r,1))
                    row['Store Name'] = str(ws.cell_value(r,2))
                    row['Store ID'] = str(ws.cell_value(r,3))
                    row['Owner'] = current_user.email
                    mongo.db.alias.update({'Alias':str(ws.cell_value(r,1))},row,upsert=True)
                    r+=1
                flash('Imported ' + str(col_len-2) + ' Alias Records')
                mongo.db.mongo_user.update({'email':current_user.email},{'$set':{'alias_updated':nice_now}})
            return redirect(url_for('import.UserProfile'))


        return self.render('admin/user_profile.html', inventoryform=inventoryform, aliasform=aliasform, apiform=apiform)

class SalesView(BaseView):
    @expose('/',methods=('GET', 'POST'))
    def index(self):
        formvalue = False
        if request.method == 'POST':
            formvalue = request.form.get('daterange')

        start, end = DateFormHanlder(formvalue)
        start_date = start.strftime('%m/%d/%Y')
        end_date = end.strftime('%m/%d/%Y')
        delta_range = (end - start).days

        date_dict, labels = DateDictBuilder(start, end)

        email = current_user.email
        ### Queries ###

        #Build Alias Dictionary - all owner alias values
        alias_search = mongo.db.alias.find({'Owner':email})
        alias_dict = AliasDictBuilder(alias_search)

        ## Find Owner Orders in Date Range
        range_search = mongo.db.orders.find({'$and':[
        {'paymentDate':{'$lte': end, '$gte':start}},
        {'owner':email}]})

        item_sku = 'All'
        item_chart, values, shipped_to_amz = ItemChartBuilder(range_search, date_dict, alias_dict, item_sku)

        top_bar = []
        sales_total = 0
        qty_total = 0
        # Build top bar values

        for row in item_chart :
            sales_total += row[2]
            qty_total += row[3]

            burn = float(row[3])/(delta_range+1)
            burn = round(burn,2)

            row.append(burn)

        #sales_total = locale.currency(sales_total)
        top_bar.append(sales_total)
        top_bar.append(qty_total)
        top_bar.append(shipped_to_amz)
        burn_rates = []

        for row in item_chart :
            row[2]= locale.currency(row[2])
            burn_rates.append(row[4])

        if len(burn_rates) > 0 :
            avg_burn = sum(burn_rates)/len(burn_rates)
            avg_burn = avg_burn*delta_range
            avg_burn = round(avg_burn,2)
        else :
            avg_burn = 0

        top_bar.append(avg_burn)
        max_values = max(values)

        return self.render('admin/sales_index.html',top=top_bar,orders=item_chart, max=max_values, labels=labels, values=values, daterange=formvalue, startdate= start_date, enddate=end_date)

class InventoryView(BaseView):
    @expose('/',methods=('GET', 'POST'))
    def InventoryCharts(self):

        i = 0
        values = []
        labels = []
        orders= []
        shipped_to_amz = 0
        # Sku, Name, Sales, Qty
        items_chart = []
        sku_list = []

        top_bar = []
        qty_total = 0
        total_value = 0
        sku_count = 0
        inventory = mongo.db.inventory.find()
        for item in inventory :
            row = []
            try:
                qty_total += int(item['Stock'])
            except:
                continue
            if item['Avg Cost'] > 0 :
                total_value += item['Avg Cost']*item['Stock']
            else :
                if "Socks" in item['Product Name'] :
                    total_value += 1.25*item['Stock']
                else :
                    total_value += .75*item['Stock']
            sku_count+=1
            row.append(item['SKU'])
            row.append(item['Product Name'])
            row.append(int(item['Stock']))
            items_chart.append(row)

        top_bar.append(qty_total)
        top_bar.append(round(total_value,2))
        top_bar.append(sku_count)


        return self.render('admin/inventory_index.html', items_chart=items_chart, top=top_bar)

class ProductView(BaseView):
    @expose('/',methods=('GET', 'POST'))
    #def is_visible(self):
        #return False
    def ProductIndex(self) :
        formvalue = False
        if request.method == 'POST':
            formvalue = request.form.get('daterange')

        start, end = DateFormHanlder(formvalue)
        start_date = start.strftime('%m/%d/%Y')
        end_date = end.strftime('%m/%d/%Y')
        delta_range = (end - start).days
        date_dict, labels = DateDictBuilder(start, end)

        email = current_user.email
        ### Queries ###

        #Build Alias Dictionary - all owner alias values
        alias_search = mongo.db.alias.find({'Owner':email})
        alias_dict = AliasDictBuilder(alias_search)

        ## Find Owner Orders in Date Range
        range_search = mongo.db.orders.find({'$and':[
        {'paymentDate':{'$lte': end, '$gte':start}},
        {'owner':email}]})

        item_sku = 'All'
        item_chart, values, shipped_to_amz = ItemChartBuilder(range_search, date_dict, alias_dict, item_sku)

        top_bar = []
        sales_total = 0
        qty_total = 0
        # Build top bar values

        ## We do not want values per day, we want value per item, graphed
        sales_values = []
        qty_values = []
        labels = []
        item_chart = sorted(item_chart, key=lambda x: x[2], reverse=True)

        for row in item_chart :
            sales_total += row[2]
            if row[2] < 0:
                continue
            labels.append(row[1])

            qty_total += row[3]
            sales_values.append(row[2])
            qty_values.append(row[3])

        range_search.rewind()
        num_orders = range_search.count()
        avg_order_size = float(qty_total/num_orders)

        #### Placeholder for 2 chart handling
        values = sales_values
        ####

        top_bar.append(sales_total)
        top_bar.append(qty_total)
        top_bar.append(num_orders)
        top_bar.append(float(qty_total)/float(num_orders))
        max_values = max(sales_values)
        return self.render('admin/product_index.html',  top=top_bar,orders=item_chart, max=max_values, labels=labels, values=values, daterange=formvalue, startdate= start_date, enddate=end_date)


    @expose('/<string:item_sku>',methods=('GET', 'POST'))
    def ProductChart(self, item_sku):
        ## Date Handling
        formvalue = False
        if request.method == 'POST':
            formvalue = request.form.get('daterange')
        #{'created':{'$lt':datetime.datetime.now(), '$gt':datetime.datetime.now() - timedelta(days=10)}}
        #return str(mdb)
        product_details = {}

        ### Build a Product Detail view across API models
        details = mongo.db.products.find_one({'sku':item_sku})
        #product_details['created'] = details['createDate']
        #product_details['category'] = details['productCategory']['name']
        product_details['sku'] = item_sku
        #inv_details = mongo.db.inventory.find_one({'SKU':item_sku})
        #product_details['stock'] = inv_details['Stock']
        #product_details['avg cost'] = inv_details['Avg Cost']

        start, end = DateFormHanlder(formvalue)
        start_date = start.strftime('%m/%d/%Y')
        end_date = end.strftime('%m/%d/%Y')
        delta_range = (end - start).days

        date_dict, labels = DateDictBuilder(start, end)

        # Sku, Name, Sales, Qty
        name = False
        img = False
        email = current_user.email

        # Individual Product Alias
        alias_search = mongo.db.alias.find({"$and":[{'Owner':current_user.email},{'Product SKU': item_sku}]})
        alias_dict = AliasDictBuilder(alias_search)
        alias_list = list(alias_dict.keys())
        alias_list.append(item_sku)

        range_search = mongo.db.orders.find(
            {'$and':[
                {'paymentDate':{'$lt': end, '$gt':start}},
                {'owner':email},
                {'items.sku':{"$in":alias_list}}
                ]})

        item_chart, values, shipped_to_amz = ItemChartBuilder(range_search, date_dict, alias_dict, item_sku)

        clean_chart = []
        for row in item_chart :
            if row[0] not in alias_list:
                continue
            else :
                clean_chart.append(row)
        item_chart = clean_chart
        ## The first time we see the item, get the name
        range_search.rewind()
        for order in range_search :
            for item in order['items'] :
                if not name or not img:
                    if item['sku'] in alias_list:
                        name = item['name']
                        img = item['imageUrl']
                else :
                    continue

        top_bar = []
        sales_total = 0
        qty_total = 0

        for row in item_chart :
            sales_total += row[2]
            qty_total += row[3]

        top_bar.append(sales_total)
        top_bar.append(qty_total)
        top_bar.append(shipped_to_amz)
        avg_burn = qty_total / delta_range
        top_bar.append(avg_burn)

        max_values = max(values)

        product_details['name'] = name
        product_details['img'] = img

        return self.render('admin/product_sku.html', product_details=product_details, alias_list=alias_list, top=top_bar,orders=item_chart, max=max_values, labels=labels, values=values, daterange=formvalue, startdate= start_date, enddate=end_date)


        #return self.render('sales_index.html')

# Flask views
@app.route('/')
def index():

    return render_template('index.html')

    #amazon = mongo.db.orders.find({'shipTo':{'name':{'$regex':'.*Amazon.*'}}})
    #amazon_list = []
    #for item in amazon:
    #    amazon_list.append(item)
colors = [
    "#F7464A", "#46BFBD", "#FDB45C", "#FEDCBA",
    "#ABCDEF", "#DDDDDD", "#ABCABC", "#4169E1",
    "#C71585", "#FF4500", "#FEDCBA", "#46BFBD"]

@app.route('/bar')
def bar():
    bar_labels=labels
    bar_values=values
    return render_template('bar_chart.html', title='Bitcoin Monthly Price in USD', max=17000, labels=bar_labels, values=bar_values)

#@app.route('/sales')
#def line():
#    line_labels=labels
#    line_values=values
#    return render_template('line_chart.html', title='Sales', max=max(values)*1.2, labels=line_labels, values=line_values, orders=items_chart, top=top_bar)




# Create admin
admin = flask_admin.Admin(
    app, name='Stockboy',
    base_template='my_master.html',
    template_mode='bootstrap3',
    url = '/dashboard',
     category_icon_classes={
        'Home': 'fa fa-line-chart'
    }
    #index_view = DashboardView()

)

# Add model views
#admin.add_view(MyModelView(MongoRole, 'Roles', menu_icon_type='fa', menu_icon_value='fa-server', name="Roles"))
#admin.add_view(UserView(MongoUser, 'Users', menu_icon_type='fa', menu_icon_value='fa-users', name="Users"))
admin.add_view(SalesView(name="Sales", endpoint='sales', menu_icon_type='fa', menu_icon_value='fa-line-chart'))
admin.add_view(InventoryView(name="Inventory", endpoint='inventory', menu_icon_type='fa', menu_icon_value='fa-archive'))
admin.add_view(ProductView(name='Products', endpoint='product', menu_icon_type='fa', menu_icon_value='fa-shopping-bag'))

#admin.add_view(AmazonView(name="Amazon", endpoint='amazon', menu_icon_type='fa', menu_icon_value='fa-amazon'))
#admin.add_view(BurnView(name="Burn", endpoint='burn', menu_icon_type='fa', menu_icon_value='fa-free-code-camp'))
admin.add_view(ProfileView(name='Settings & Import', endpoint='import', menu_icon_type='fa', menu_icon_value='fa-cog'))

## Sub Items - Not visible in menu
#admin.add_view(UserView(mongo.db['users'],mongo.db, endpoint='Mongo'))
# define a context processor for merging flask-admin's template context into the
# flask-security views.
@security.context_processor
def security_context_processor():
    return dict(
        admin_base_template=admin.base_template,
        admin_view=admin.index_view,
        h=admin_helpers,
        get_url=url_for
    )


if __name__ == '__main__':

    # Build a sample db on the fly, if one does not exist yet.
    app_dir = os.path.realpath(os.path.dirname(__file__))
    #database_path = os.path.join(app_dir, app.config['DATABASE_FILE'])


    # Start app
    app.run(debug=True)
