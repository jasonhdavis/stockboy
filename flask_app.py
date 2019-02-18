#!venv/bin/python
# -*- coding: utf-8 -*-

import os
import time
import csv
from datetime import datetime, timedelta
import locale
locale.setlocale(locale.LC_ALL, '')
from flask import Flask, url_for, redirect, render_template, request, abort, flash, send_from_directory
from flask_mongoengine import MongoEngine
from flask_security import Security, \
    UserMixin, RoleMixin, login_required, current_user, datastore, MongoEngineUserDatastore
from flask_security.utils import encrypt_password
import hashlib
import base64
from flask_mail import Mail
import flask_admin
from flask_admin.contrib import sqla
from flask_admin import helpers as admin_helpers
from flask_admin import BaseView, expose, AdminIndexView
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

from flask_login import LoginManager


import xlrd



# Create Fldask application
app = Flask(__name__)
app.config.from_pyfile('config.py')

Bootstrap(app)
datepicker(app)

mongo = PyMongo(app)
enginedb = MongoEngine(app)
mail = Mail(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

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

    for i in range(delta_range+1):
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

#    cursor.rewind()

#    alias_dict['reverse'] = {}
#    reverse = alias_dict['reverse']
#    for alias in cursor :
#        product_sku = alias['Product SKU']
#        alias = alias['Alias']
#        if alias in reverse :
#        alias_dict['reverse'][alias['Alias']]

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

def ItemDictBuilder(cursor, date_dict, alias_dict, item_sku):
    shipped_to_amz = 0
    item_dict = {}
    sku_list = []
    for order in cursor :
        # Sum Order Totals
        order_date = order['orderDate']

        # Count ship to Amz Quantity to avoid
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

            store_id = order['advancedOptions']['storeId']

            if sku in item_dict :
                item_dict[sku]['sales'] += sales
                item_dict[sku]['qty'] += qty
                item_dict[sku]['stores'] = {}
                if store_id in item_dict :
                    item_dict[sku]['stores'][store_id] += qty
                else :
                    item_dict[sku]['stores'][store_id]={}
                    item_dict[sku]['stores'][store_id] = qty

                if sku == item_sku or item_sku == 'All':
                    date_dict[order_date.year][order_date.month][order_date.day]+= sales

            else :
                item_dict[sku] = {}
                item_dict[sku]['name'] = name
                item_dict[sku]['sales'] = sales
                item_dict[sku]['qty'] = qty
                item_dict[sku]['stores'] = {store_id:qty}

                if sku == item_sku or item_sku == 'All' :
                    date_dict[order_date.year][order_date.month][order_date.day]+= sales

    #Build chart value list
    values=[]

    for year in date_dict.values():
        for month in year.values():
          for day in month.values() :
              values.append(day)

    return item_dict, values, shipped_to_amz

def ItemChartBuilder(cursor, date_dict, alias_dict, item_sku):
    shipped_to_amz = 0
    item_chart = []
    sku_list = []
    for order in cursor :
        # Sum Order Totals
        order_date = order['orderDate']
        amz_loc = ['24208 SAN MICHELE','900 PATROL','10240 OLD DOWD','705 BOULDER DR','6835 W BUCKEYE']
        # Count ship to Amz Quantity to avoid
        ship_to = order['shipTo']['name']

        ship_add = order['shipTo']['street1']
        if ship_to.find('Amazon') >-1 or ship_to.find('GOLDEN STATE') >-1:
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

            store_id = order['advancedOptions']['storeId']

            if sku in sku_list :
                sku_idx = sku_list.index(sku)
                item_chart[sku_idx][2] += sales
                item_chart[sku_idx][3] += qty
                if sku == item_sku or item_sku == 'All':
                    date_dict[order_date.year][order_date.month][order_date.day]+= sales

            else :
                row.append(sku)
                row.append(name)
                row.append(sales)
                row.append(qty)
                sku_list.append(sku)
                item_chart.append(row)
                if sku == item_sku or item_sku == 'All' :
                    date_dict[order_date.year][order_date.month][order_date.day]+= sales

    #Build chart value list
    values=[]

    for year in date_dict.values():
        for month in year.values():
          for day in month.values() :
              values.append(day)

    return item_chart, values, shipped_to_amz

def FBADictBuilder() :
    cursor = mongo.db.fba.find({'Owner':current_user.email})
    fba_dict = {}
    for item in cursor :
        asin = item['asin']

        if asin not in fba_dict :
            fba_dict[asin] = {}

            fba_dict[asin]['afn-fulfillable-quantity'] = 0
            fba_dict[asin]['afn-warehouse-quantity'] = 0
            fba_dict[asin]['afn-total-quantity'] = 0
            fba_dict[asin]['afn-inbound-shipped-quantity'] = 0
            fba_dict[asin]['afn-reserved-quantity'] = 0

        fba_dict[asin]['afn-fulfillable-quantity'] += int(item['afn-fulfillable-quantity'])
        fba_dict[asin]['afn-warehouse-quantity'] += int(item['afn-warehouse-quantity'])
        fba_dict[asin]['afn-total-quantity'] += int(item['afn-total-quantity'])
        fba_dict[asin]['afn-inbound-shipped-quantity'] += int(item['afn-inbound-shipped-quantity'])
        fba_dict[asin]['afn-reserved-quantity'] += int(item['afn-reserved-quantity'])

    return fba_dict

def ShipmentDictBuilder(start, end) :
    cursor = mongo.db.shipments.find({'$and':[
    {'createDate':{'$lte': end, '$gte':start}},
    {'owner':current_user.email}]})

    ship_dict = {}
    for item in cursor :
        order_id = item['orderId']
        if order_id not in ship_dict :
            ship_dict[order_id] = {}
            sd = ship_dict[order_id]
            sd['shipmentCost'] = item['shipmentCost']
            sd['name'] = item['shipTo']['name']
            sd['createDate'] = item['createDate']
            sd['carrierCode'] = item['carrierCode']
            sd['trackingNumber'] = item['trackingNumber']
        else :
            ## Spilt shipment / multiple shipments for one order
            sd = ship_dict[order_id]
            sd['shipmentCost'] += item['shipmentCost']
            if sd['createDate'] < item['createDate'] :
                sd['createDate'] = item['createDate']
                sd['carrierCode'] = item['carrierCode']
                sd['trackingNumber'] = item['trackingNumber']

    return ship_dict

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
    fba_updated = enginedb.DateTimeField()

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
    @login_required
    def UserProfile(self):
        apiform = SSAPI(prefix='apiform')
        inventoryform = FileUploadForm(prefix='inventoryform')
        aliasform = FileUploadForm(prefix='aliasform')
        fbaform = FileUploadForm(prefix='fbaform')
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
                mongo.db.inventory.remove({"Owner":current_user.email})
                col_len = len(ws.col(0))
                r = 8
                update_count = 0
                while r < col_len :
                    sku = str(ws.cell_value(r,1))
                    name = ws.cell_value(r,2).encode('utf8')
                    if sku == "":
                        #Shipstation merges name column vertically
                        # Leaving  blank sku cells below
                        r = r+1
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
                    r = r+1
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
                mongo.db.alias.remove({"Owner":current_user.email})

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
                mongo.db.mongo_user.update({'email':current_user.email},{'$set':{'alias_updated':now}})
            return redirect(url_for('import.UserProfile'))

        if fbaform.validate_on_submit() and fbaform.submit.data:
            f = fbaform.file.data
            filename = secure_filename(f.filename)
            save_path = os.path.join(app.root_path,'uploads', filename)
            f.save(save_path)

            fba = csv.DictReader(open(save_path, 'rt', encoding='ISO-8859-1'), delimiter='\t')
            text = []
            lines = 0
            mongo.db.fba.remove({"Owner":current_user.email})
            for line in fba :
                try:
                    encoded_line = {k: unicode(v).decode("utf-8") for k,v in line.iteritems()}
                except:
                    try:
                        encoded_line = {k: unicode(v).decode("utf-16") for k,v in line.iteritems()}
                    except:
                        #print encoded_line
                        continue
                try:
                    encoded_line['Owner'] = current_user.email
                    encoded_line['fba_updated'] = nice_now
                    mongo.db.fba.update({'asin':line['asin']},encoded_line,upsert=True)

                    #mongo.db.fba.update({'email':current_user.email},{'$set':{'sku':line['sku']}},encoded_line,upsert=True)
                except:
                    print("line write error")
                    flash(encoded_line)

                lines+= 1

            mongo.db.mongo_user.update({'email':current_user.email},{'$set':{'fba_updated':now}})
            flash('Imported ' + str(lines) + ' FBA Records')

            return redirect(url_for('import.UserProfile'))


        return self.render('admin/user_profile.html', inventoryform=inventoryform, aliasform=aliasform, apiform=apiform, fbaform=fbaform)

class SalesView(BaseView):
    @expose('/',methods=('GET', 'POST'))
    @login_required
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
        {'orderDate':{'$lte': end, '$gte':start}},
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

        return self.render('admin/sales_index.html',top=top_bar,orders=item_chart, labels=labels, values=values, daterange=formvalue, startdate= start_date, enddate=end_date)

class InventoryView(BaseView):
    @expose('/',methods=(['GET']))
    @login_required
    def InventoryCharts(self):
        i = 0
        values = []
        labels = []
        orders= []
        shipped_to_amz = 0
        # Sku, Name, Sales, Qty
        items_chart =    []
        sku_list = []

        top_bar = []
        qty_total = 0
        total_value = 0
        sku_count = 0

        formvalue = False
        start, end = DateFormHanlder(formvalue)
        start = start - timedelta(days=15)
        start_date = start.strftime('%m/%d/%Y')
        end_date = end.strftime('%m/%d/%Y')
        delta_range = (end - start).days
        date_dict, labels = DateDictBuilder(start, end)

        email = current_user.email

        #Build Alias Dictionary - all owner alias values
        alias_search = mongo.db.alias.find({'Owner':email})
        alias_dict = AliasDictBuilder(alias_search)

        ## Find Owner Orders in Date Range
        range_search = mongo.db.orders.find({'$and':[
        {'orderDate':{'$lte': end, '$gte':start}},
        {'owner':email}]})

        item_sku = 'All'
        item_sales_chart, values, shipped_to_amz = ItemChartBuilder(range_search, date_dict, alias_dict, item_sku)
        sku_sales_index = []
        total_qty_sold = 0

        for item in item_sales_chart :
            sku_sales_index.append(item[0])
            total_qty_sold += item[3]

        fba_dict = FBADictBuilder()

        inventory = mongo.db.inventory.find({'Owner':email})

        for item in inventory :
            row = []
            amz_qty = 0
            sku_count+=1
            matched_asin = False
            sku = item['SKU']
            alias_list = []
            item_amz_warehouse = 0
            item_amz_inbound = 0

            for k,v in alias_dict.items() :
                if v == sku :
                    alias_list.append(k)

            local_qty = item['Stock']
            qty_total += int(local_qty)
            true_count = 0

            for alias in alias_list :
                if alias in fba_dict :
                    matched_asin = True
                    item_amz_warehouse = int(fba_dict[alias]['afn-warehouse-quantity'])
                    item_amz_inbound = int(fba_dict[alias]['afn-inbound-shipped-quantity'])
                    amz_qty = item_amz_warehouse + item_amz_inbound
                    qty_total += amz_qty
                    true_count +=1
                    if true_count > 1 :
                        flash('Multiple ASIN Match on: '+ item['Product Name'])


            if item['Avg Cost'] > 0 :
                total_value += item['Avg Cost']*(local_qty + amz_qty)

            else :
                if "Socks" in item['Product Name'] :
                    ## Placeholder for inventory value
                    total_value += 1.25*int(local_qty+amz_qty)
                else :
                    ## Placeholder for inventory value
                    total_value += .75*int(local_qty+amz_qty)


            row.append(sku)
            row.append(item['Product Name'])
            row.append(int(item['Stock']))
            if matched_asin :
                row.append(item_amz_warehouse)
                row.append(item_amz_inbound)
            else :
                row.append(0)
                row.append(0)

            ## FBA

            if sku in sku_sales_index :
                sales_idx = sku_sales_index.index(sku)
                qty_sold = item_sales_chart[sales_idx][3]
            else :
                qty_sold = 0

            row.append(qty_sold)
            burn = float(qty_sold)/float(delta_range)

            if burn == 0:
                inventory_days = 9999999
            else :
                inventory_days = int(local_qty+amz_qty) / burn


            row.append(inventory_days)
            items_chart.append(row)


        top_bar.append(qty_total)
        top_bar.append(round(total_value,2))
        top_bar.append(sku_count)
        sell_through_rate = float(total_qty_sold) / float(total_qty_sold+qty_total)
        top_bar.append(sell_through_rate*100)


        return self.render('admin/inventory_index.html', items_chart=items_chart, top=top_bar)

    @expose('/fba',methods=('GET', 'POST'))
    def FBAInventory(self):
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

        formvalue = False
        start, end = DateFormHanlder(formvalue)
        start = start - timedelta(days=15)
        start_date = start.strftime('%m/%d/%Y')
        end_date = end.strftime('%m/%d/%Y')
        delta_range = (end - start).days
        date_dict, labels = DateDictBuilder(start, end)

        email = current_user.email

        #Build Alias Dictionary - all owner alias values
        alias_search = mongo.db.alias.find({'Owner':email})
        alias_dict = AliasDictBuilder(alias_search)

        ## Find Owner Orders in Date Range
        range_search = mongo.db.orders.find({'$and':[
        {'orderDate':{'$lte': end, '$gte':start}},
        {'owner':email}]})

        item_sku = 'All'
        item_dict, values, shipped_to_amz = ItemDictBuilder(range_search, date_dict, alias_dict, item_sku)
        total_qty_sold = 0

        fba_inventory = mongo.db.fba.find()

        fba_dict = {}
        for fba_item in fba_inventory :
            asin = fba_item['asin']
            fba_dict[asin]={}
            for k, v in fba_item.iteritems() :
                fba_dict[asin][k] = v

        #inventory = mongo.db.inventory.find({'Owner':email})

        for item in fba_inventory :
            row = []
            amz_qty = 0
            sku_count+=1
            matched_asin = False
            sku = item['SKU']

            for k,v in alias_dict.iteritems() :
                if v == sku :
                    test_asin = k
                    if test_asin in fba_dict :
                        matched_asin = test_asin

            #local_qty = item['Stock']
            #qty_total += int(local_qty)

            if matched_asin :
                amz_qty = int(fba_dict[matched_asin]['afn-fulfillable-quantity'])
                qty_total += amz_qty

            if item['Avg Cost'] > 0 :
                total_value += item['Avg Cost'] * amz_qty
            else :
                if "Socks" in item['Product Name'] :
                    ## Placeholder for inventory value
                    total_value += 1.25*int(local_qty+amz_qty)
                else :
                    ## Placeholder for inventory value
                    total_value += .75*int(local_qty+amz_qty)


            row.append(sku)
            row.append(item['Product Name'])
            if matched_asin :
                row.append(amz_qty)
                row.append(fba_dict[matched_asin]['afn-inbound-shipped-quantity'])
            else :
                row.append(0)
                row.append(0)

            #Amazon Sales only - may conflate Merchant & Amazon fullfilled
            qty_sold = item_dict[sku]['stores'][226766]
            total_qty_sold += qty_sold

            row.append(qty_sold)
            burn = float(qty_sold)/float(delta_range)

            if burn == 0:
                inventory_days = 9999999
            else :
                inventory_days = int(local_qty+amz_qty) / burn

            row.append(inventory_days)
            items_chart.append(row)


        top_bar.append(qty_total)
        top_bar.append(round(total_value,2))
        top_bar.append(sku_count)
        sell_through_rate = float(total_qty_sold) / float(total_qty_sold+qty_total)
        top_bar.append(sell_through_rate*100)


        return self.render('admin/inventory_index.html', items_chart=items_chart, top=top_bar)

class ProductView(BaseView):
    @expose('/',methods=('GET', 'POST'))
    @login_required
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
        {'orderDate':{'$lte': end, '$gte':start}},
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
                {'orderDate':{'$lt': end, '$gt':start}},
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
        avg_burn = float(qty_total) / float(delta_range)
        top_bar.append(avg_burn)

        max_values = max(values)

        product_details['name'] = name
        product_details['img'] = img

        return self.render('admin/product_sku.html', product_details=product_details, alias_list=alias_list, top=top_bar,orders=item_chart, max=max_values, labels=labels, values=values, daterange=formvalue, startdate= start_date, enddate=end_date)

class CustomerView(BaseView) :
    @expose('/',methods=('GET', 'POST'))
    @login_required
    def CustomerIndex(self) :
        formvalue = False
        if request.method == 'POST':
            formvalue = request.form.get('daterange')

        ## Date range builder
        start, end = DateFormHanlder(formvalue)
        start_date = start.strftime('%m/%d/%Y')
        end_date = end.strftime('%m/%d/%Y')
        delta_range = (end - start).days

        email = current_user.email
        order_value_list = []
        #Query - get orders for current users in date range
        range_search = mongo.db.orders.find({'$and':[
        {'orderDate':{'$lte': end, '$gte':start}},
        {'owner':email}]})
        num_orders = range_search.count()

        # Returns dictionary of orders, centered around customer details
        customer_dict = {}
        for order in range_search :
            if 'customerId' in order.keys():
                customer_id = order['customerId']
            else :
                customer_id = None

            if customer_id is None :
                # Amazon orders do not have a customer ID
                # [DONE] Create identifyable hash of the Street Address + Zip code
                # Create a customer ID with 'AMZ' prefix
                # Update MongoDB with both values
                address = order['shipTo']['street1']+'::'+order['shipTo']['postalCode']
                customer_id = base64.urlsafe_b64encode(hashlib.md5(address.encode('utf8')).digest())
                customer_id=customer_id.decode('ascii')

                #mongo.db.orders.update({'_id' : order['_id']},{'customerId':customer_id})

            if customer_id not in customer_dict :
                customer_dict.update({customer_id:{
                'num_orders': 0,
                'customer_value': 0
                }})

            customer_dict[customer_id]['name'] = order['shipTo']['name']
            customer_dict[customer_id]['city'] = order['shipTo']['city']
            customer_dict[customer_id]['state'] = order['shipTo']['state']
            customer_dict[customer_id]['email'] = order['customerEmail']
            customer_dict[customer_id]['num_orders'] +=1
            customer_dict[customer_id]['customer_value'] += order['orderTotal']
            order_value_list.append(order['orderTotal'])

        unique_customers = len(customer_dict)
        avg_order_value = sum(order_value_list) / len(order_value_list)
        repeat_rate = float(num_orders)/float(unique_customers)
        top_bar=[]
        top_bar.append(num_orders)
        top_bar.append(unique_customers)
        top_bar.append(avg_order_value)
        top_bar.append(repeat_rate)


        return self.render('admin/customer_index.html', customer_dict=customer_dict, top=top_bar, daterange=formvalue, startdate= start_date, enddate=end_date)

    @expose('/<customer_id>',methods=('GET', 'POST'))
    def CustomerDetails(self, customer_id):

        # Query orders for customer ID
        # Build sales & item chart based on query

        email = current_user.email
        alias_search = mongo.db.alias.find({'Owner':email})
        alias_dict = AliasDictBuilder(alias_search)

        try:
            customer_id = int(customer_id)
        except:
            pass

        customer_search = mongo.db.orders.find({'customerId':customer_id})

        num_orders = customer_search.count()
        customer_order_history = []
        customer_dict = {
            'customer_value' : 0
        }
        for order in customer_search :
            customer_order_history.append(order['orderDate'])
            customer_dict['name'] = order['shipTo']['name']
            customer_dict['street1'] = order['shipTo']['street1']
            customer_dict['city'] = order['shipTo']['city']
            customer_dict['state'] = order['shipTo']['state']
            customer_dict['email'] = order['customerEmail']
            customer_dict['customer_value'] += order['orderTotal']


        customer_search.rewind()
        customer_since = min(customer_order_history)
        #customer_since = datetime.strptime(customer_since,'%Y-%m-%dT%H:%M:%S')

        item_sku= 'All'

        start = customer_since
        end = today
        delta_range = (end - start).days

        date_dict, labels = DateDictBuilder(start, end)

        item_chart, values, shipped_to_amz = ItemChartBuilder(customer_search, date_dict, alias_dict, item_sku)
        top_bar = []

        # Customer Value

        # Number of Orders
        # Customer Since
        # Frequency
        order_frequency = delta_range/num_orders

        top_bar.append(customer_dict['customer_value'])
        top_bar.append(num_orders)
        customer_since_date_str = str(customer_since.month) +"/"+ str(customer_since.day) +"/"+ str(customer_since.year)
        top_bar.append(customer_since_date_str)
        top_bar.append(order_frequency)

        return self.render('admin/customer_details.html', top=top_bar, orders=item_chart, labels=labels, values=values, customer_dict=customer_dict)

class ShipmentView(BaseView):
    @expose('/',methods=('GET', 'POST'))
    @login_required
    #def is_visible(self):
        #return False
    def index(self):
        formvalue = False
        if request.method == 'POST':
            formvalue = request.form.get('daterange')

        start, end = DateFormHanlder(formvalue)
        start_date = start.strftime('%m/%d/%Y')
        end_date = end.strftime('%m/%d/%Y')
        delta_range = (end - start).days

        date_dict, labels = DateDictBuilder(start, end)

        ship_dict = ShipmentDictBuilder(start, end)

        shipment_range_order_ids = list(ship_dict.keys())
        order_cursor = mongo.db.orders.find({'orderId':{'$in':shipment_range_order_ids}})

        ## Order Dict Builder
        order_dict = {}
        for order in order_cursor :
            order_id = order['orderId']
            order_dict[order_id] = {}
            od = order_dict[order_id]

            od['orderDate'] = order['orderDate']
            order_qty = 0

            if 'items' in order.keys():
                for item in order['items']:
                    order_qty += item['quantity']

            od['orderQty'] = order_qty

        shipment_count = 0
        handle_times = []
        shipping_times = []
        shipment_chart = []
        shipment_costs = []
        qty_total = 0
        for shipment in ship_dict :
            row = []
            order_id = int(shipment)
            row.append(order_id)
            row.append(ship_dict[shipment]['name'])
            try:
                order_payment_timestamp = order_dict[order_id]['orderDate']
            except:
                flash(str(order_id) +" Not able to include this order for some reason")
                continue
            create_date = ship_dict[shipment]['createDate']

            handle_time = create_date - order_payment_timestamp

            days = handle_time.days
            seconds = handle_time.total_seconds()
            hours = seconds // 3600
            hours = hours - (days*24)
            minutes = (seconds % 3600) // 60
            seconds = seconds % 60
            handle_time_string = '{} days, {} hours'.format(days, hours)
            row.append(handle_time_string)
            if handle_time.total_seconds() != 0:
                handle_times.append(handle_time)

            if 'actual_delivery_date' in ship_dict[shipment].keys() :
                shipping_time = ship_dict[shipment]['actual_delivery_date'] - create_date
                shipping_times.append(shipping_time)
            else :
                shipping_time = 'NA'

            row.append(shipping_time)

            order_qty = order_dict[order_id]['orderQty']
            row.append(order_qty)
            qty_total += order_qty

            row.append(ship_dict[shipment]['shipmentCost'])
            shipment_costs.append(ship_dict[shipment]['shipmentCost'])

            shipment_count += 1
            shipment_chart.append(row)

            date_dict[create_date.year][create_date.month][create_date.day]+= 1

        values = []
        for year in date_dict.values() :
            for month in year.values() :
                for day in month.values() :
                    values.append(day)



        top_bar = []
        top_bar.append(shipment_count)

        average_handletime = sum(handle_times, timedelta(0)) / len(handle_times)
        #average_handletime = average_handletime.hours
        #days = handle_time.days
        seconds = average_handletime.total_seconds()
        hours = int(seconds) / 3600
        hours = hours - (days*24)
        #minutes = (seconds % 3600) // 60
        #seconds = seconds % 60
        avg_handle_time_string = '{} days, {} hours'.format(days, hours)

        top_bar.append(avg_handle_time_string)
        average_cost = sum(shipment_costs)/len(shipment_costs)
        top_bar.append(average_cost)
        top_bar.append(qty_total/delta_range)

        return self.render('admin/shipment_index.html',  top=top_bar,orders=shipment_chart,  daterange=formvalue, startdate= start_date, enddate=end_date, labels=labels, values=values)

# Flask views
index_file_dir = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'homepage')
assets_file_dir = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'homepage/assets')

@app.route('/', methods=['GET'])
def homepage():
    return send_from_directory(index_file_dir, 'index.html')


@app.route('/assets/<path:path>', methods=['GET'])
def serve_file_in_dir(path):

    if not os.path.isfile(os.path.join(assets_file_dir, path)):
        path = os.path.join(index_file_dir, 'index.html')

    path, filename = os.path.split(path)
    request_file_dir = os.path.join(assets_file_dir,path)

    return send_from_directory(request_file_dir, filename)

    #amazon = mongo.db.orders.find({'shipTo':{'name':{'$regex':'.*Amazon.*'}}})
    #amazon_list = []
    #for item in amazon:
    #    amazon_list.append(item)
colors = [
    "#F7464A", "#46BFBD", "#FDB45C", "#FEDCBA",
    "#ABCDEF", "#DDDDDD", "#ABCABC", "#4169E1",
    "#C71585", "#FF4500", "#FEDCBA", "#46BFBD"]


# Create admin
admin = flask_admin.Admin(
    app, name='Stockboy',
    base_template='my_master.html',
    template_mode='bootstrap3',
    url = '/dashboard',
    index_view=AdminIndexView(
    name='Dashboard',
    template='admin/index.html',
    menu_icon_type='fa',
    url = '/dashboard',
    menu_icon_value='fa-tachometer'
    )
    #index_view = DashboardView()

)

# Add model views
#admin.add_view(MyModelView(MongoRole, 'Roles', menu_icon_type='fa', menu_icon_value='fa-server', name="Roles"))
#admin.add_view(UserView(MongoUser, 'Users', menu_icon_type='fa', menu_icon_value='fa-users', name="Users"))
admin.add_view(SalesView(name="Sales", endpoint='sales', menu_icon_type='fa', menu_icon_value='fa-area-chart'))
admin.add_view(InventoryView(name="Inventory", endpoint='inventory', menu_icon_type='fa', menu_icon_value='fa-archive'))
admin.add_view(ProductView(name="Products", endpoint='product', menu_icon_type='fa', menu_icon_value='fa-shopping-bag'))
admin.add_view(CustomerView(name="Customers", endpoint='customers', menu_icon_type='fa', menu_icon_value='fa-users'))
admin.add_view(ShipmentView(name="Shipments", endpoint='shipments', menu_icon_type='fa', menu_icon_value='fa-truck'))
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
