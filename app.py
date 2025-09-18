from flask import Flask, request, jsonify, render_template, redirect, url_for, session, flash
import mysql.connector
from datetime import datetime, date
import os
import sys
print(os.urandom(24))

app = Flask(__name__)
app.secret_key = b'\xb9uO\x0c@\xb5\xa4>\xf7\xd8\xe4\xa9Bo\xca]\x1a\xdb\x0c\xb0\xcf\x0eSl'

# 資料庫連線設定
def get_db():
    return mysql.connector.connect(
        host="localhost",
        user="root",
        password="wang910350",
        database="wheelcake_inventory"
    )

@app.route('/')
def employee_index():
    return render_template('employee_index.html')

@app.route('/employee')
def employee_page():
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT id, name, price FROM menu_items")
    menu_items = cursor.fetchall()
    cursor.close()
    conn.close()
    return render_template('employee_home.html', menu_items=menu_items)

@app.route('/employee_dashboard')
def employee_dashboard():
    return render_template('employee_dashboard.html')


@app.route('/api/create_order', methods=['POST'])
def create_order():
    print("DEBUG: 收到 /api/create_order 請求")
    conn = get_db()
    cursor = conn.cursor()

    try:
        data = request.get_json()
        print("DEBUG 前端傳來的 JSON:", data)
        order_datetime_str = data.get('order_datetime') # 從前端獲取，應該是日期字串
        if not order_datetime_str:
            return jsonify({'message': '缺少訂單日期時間', 'success': False}), 400
        
        total_amount_str = data.get('total_amount') # 確保轉換為浮點數
        if total_amount_str is None: # 先檢查是否存在
            return jsonify({'message': '缺少訂單總金額', 'success': False}), 400
        try: # 後嘗試轉換，捕捉 ValueError
            total_amount = float(total_amount_str)
        except ValueError:
            return jsonify({'message': '訂單總金額格式不正確，必須是數字', 'success': False}), 400
        
        items = data.get('items') # 這是從前端購物車傳過來的品項列表
        if not items:
            return jsonify({'message': '訂單中沒有品項', 'success': False}), 400

        conn.start_transaction()

        # 1. 插入主訂單到 orders 表
        insert_order_query = """
            INSERT INTO orders (order_datetime, total_amount)
            VALUES (%s, %s)
        """
        cursor.execute(insert_order_query, (order_datetime_str, total_amount))
        # 如果 order_id 是自增的，您可能需要獲取最後插入的 ID
        # order_db_id = cursor.lastrowid
        order_db_id = cursor.lastrowid 
        if not order_db_id: # 檢查是否成功獲取 ID
            conn.rollback()
            return jsonify({'message': '無法獲取新訂單ID，請檢查orders表的ID是否為AUTO_INCREMENT', 'success': False}), 500


        # 2. 遍歷每個訂單品項，插入到 order_items 表並扣除食材庫存
        try:

            for item in items:
                menu_item_id = item.get('menu_item_id')

                print(f"DEBUG: item 內容 = {item}")

                quantity = 0 # 先初始化 quantity 為 0，作為安全預設值
                quantity_str = item.get('qty') # 從 item 中獲取 'qty'

                low_stock_ingredients = []  # 紀錄低於安全庫存的食材名稱

                try:
                    if quantity_str is not None: # 確保有收到值
                        quantity = int(quantity_str)
                    if quantity < 0: # 避免負數數量
                        quantity = 0
                except (ValueError, TypeError) as e:
                    # 這裡會捕捉到例如 'abc' 這樣的字串嘗試轉換為 int 的錯誤
                    print(f"ERROR: 數量轉換失敗，收到: '{quantity_str}'。錯誤: {e}。將設為 0。")
                    quantity = 0 # 轉換失敗，設為 0

                    price_str = item.get('price')
                    price_per_item = float(price_str) if price_str is not None else 0.0

                    print(f"DEBUG: 接收到的 menu_item_id: {menu_item_id}, 品項: {item}")

                if not menu_item_id:
                    conn.rollback()
                    return jsonify({'message': '品項缺少 menu_item_id，無法處理', 'success': False}), 400

                cursor.execute("""
                    INSERT INTO order_items (order_id, menu_item_id, quantity)
                    VALUES (%s, %s, %s)
                """, (order_db_id, menu_item_id, quantity))

                cursor.execute("""
                    SELECT pi.ingredient_id, pi.quantity_needed, ii.stock_quantity
                    FROM product_ingredients pi
                    JOIN ingredient_inventory ii ON pi.ingredient_id = ii.ID
                    WHERE pi.menu_item_id = %s
                """, (menu_item_id,))
                recipe_ingredients = cursor.fetchall()

                if not recipe_ingredients:
                        print(f"WARN: 品項 '{menu_item_id}' 沒有定義食材配方，不扣除食材庫存。")
                else:
                    for ing_id, needed_per_product, current_stock in recipe_ingredients:
                        needed_per_product = float(needed_per_product) if needed_per_product is not None else 0.0
                         # total_needed = quantity_needed * stock_quantity
                        total_needed = needed_per_product * quantity

                        print(f"DEBUG: 計算 total_needed，食材 {ing_id}，所需數量 {needed_per_product}，購買數量 {quantity}，結果: {total_needed}")

                    # 先判斷庫存是否足夠，足夠就扣庫存，不夠就跳出什麼食材庫存補足的警示
                        if current_stock < total_needed:
                            conn.rollback()
                            cursor.execute("SELECT ingredient_name FROM ingredient_inventory WHERE ID = %s", (ing_id,))
                            ing_name = cursor.fetchone()[0]
                            return jsonify({'message': f'食材 "{ing_name}" 庫存不足 ({current_stock} < {total_needed})，無法完成訂單。', 'success': False}), 400
                            
                        # 庫存足夠-扣庫存
                        cursor.execute("""
                            UPDATE ingredient_inventory
                            SET stock_quantity = stock_quantity - %s
                            WHERE ID = %s
                        """, (total_needed, ing_id))

                        # 先查安全庫存
                        cursor.execute("SELECT safety_stock, ingredient_name FROM ingredient_inventory WHERE ID = %s", (ing_id,))
                        safe_stock_row = cursor.fetchone()
                        safe_stock = safe_stock_row[0] if safe_stock_row else 0
                        ing_name = safe_stock_row[1] if safe_stock_row else "未知食材"

                        # 扣完庫存後，查詢目前庫存
                        cursor.execute("SELECT stock_quantity FROM ingredient_inventory WHERE ID = %s", (ing_id,))
                        current_stock_after = cursor.fetchone()[0]

                        if current_stock_after < safe_stock:
                            low_stock_ingredients.append(ing_name)
                                                    
            conn.commit()

            if low_stock_ingredients:
                return jsonify({
                    'message': '訂單新增成功，但以下食材庫存低於安全庫存，請盡速補貨。',
                    'low_stock_items': low_stock_ingredients,
                    'success': True
                }), 201
            else:
                return jsonify({'message': '訂單新增成功', 'success': True}), 201


        except mysql.connector.Error as err:
            conn.rollback()
            print(f"資料庫錯誤: {err}")
            return jsonify({'message': f'資料庫錯誤: {err}', 'success': False}), 500

    except Exception as e:
        conn.rollback()
        print(f"新增訂單錯誤: {e}")
        return jsonify({'message': f'新增訂單失敗: {e}', 'success': False}), 500

    finally:
        cursor.close()
        conn.close()


@app.route('/add_order') # <--- 這就是渲染 add_new_order.html 的路由
def add_new_order_page():
    print("✅ add_order 路由被觸發！")
    conn = get_db()
    cursor = conn.cursor(dictionary=True) # 使用 dictionary=True 讓結果更易於訪問 (例如 item['id'])
    menu_items = [] # 初始化為空列表以防萬一

    try:
        # 執行查詢以獲取所有菜單品項，確保 'id', 'name', 'price' 都被選中
        cursor.execute("SELECT id, name, price FROM menu_items ORDER BY name")
        menu_items = cursor.fetchall()

        for item in menu_items:
            print(f"--- Debugging menu_items from Flask backend ---\nMenuItem: ID={item['id']}, Name={item['name']}, Price={item['price']}")
        print("DEBUG 菜單資料:", menu_items, file=sys.stderr)

        # **** 您要添加的 print 語句在這裡！ ****
        print("\n--- Debugging menu_items from Flask backend ---") #
        for item in menu_items: #
            print(f"MenuItem: ID={item.get('id')}, Name={item.get('name')}, Price={item.get('price')}") 
            sys.stdout.flush() # 強制刷新標準輸出
        print("---------------------------------------------------\n") #

    except mysql.connector.Error as err:
        print(f"資料庫錯誤 (add_new_order_page): {err}")
    except Exception as e:
        print(f"獲取菜單品項錯誤: {e}")
    finally:
        cursor.close()
        conn.close()

    # 將 menu_items 傳遞給模板
    return render_template('employee_home.html', menu_items=menu_items)

@app.route('/employee/orders', methods=['GET'])
def employee_orders():
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        query = """
            SELECT
                o.id AS order_id,
                o.id AS order_number_display,
                o.order_datetime,
                o.total_amount
            FROM orders o
            ORDER BY o.order_datetime DESC
        """
        cursor.execute(query)
        orders = cursor.fetchall()

        for order in orders:
            if isinstance(order['order_datetime'], (datetime, date)):
                order['order_datetime'] = order['order_datetime'].strftime('%Y-%m-%d %H:%M:%S')

            items_query = """
                SELECT
                    mi.name AS flavor,
                    oi.quantity,
                    mi.price
                FROM order_items oi
                JOIN menu_items mi ON oi.menu_item_id = mi.id
                WHERE oi.order_id = %s
            """
            cursor.execute(items_query, (order['order_id'],))
            order['items'] = cursor.fetchall()
            order['items_display'] = ", ".join([f"{item['flavor']} x {item['quantity']}" for item in order['items']])
            print(f"Debug: Fetched orders: {orders}")  # 添加除錯日誌

            for order in orders:
                if isinstance(order['order_datetime'], (datetime, date)):
                    order['order_datetime'] = order['order_datetime'].strftime('%Y-%m-%d %H:%M:%S')

                items_query = """
                    SELECT 
                        mi.name AS flavor,
                        oi.quantity,
                        mi.price
                    FROM order_items oi
                    JOIN menu_items mi ON oi.menu_item_id = mi.id
                    WHERE oi.order_id = %s
            """
            cursor.execute(items_query, (order['order_id'],))
            order['items'] = cursor.fetchall()
            order['items_display'] = ", ".join([f"{item['flavor']} x {item['quantity']}" for item in order['items']])
            print(f"Debug: Order {order['order_id']} items: {order['items']}")  # 添加除錯日誌

    except mysql.connector.Error as err:
        print(f"資料庫錯誤 (api_orders): {err}")
        return jsonify({'message': f'資料庫錯誤: {err}', 'success': False}), 500
    except Exception as e:
        print(f"後端服務錯誤 (api_orders): {e}")
        return jsonify({'message': f'後端服務錯誤: {e}', 'success': False}), 500
    finally:
        cursor.close()
        conn.close()
    return jsonify(orders)

@app.route('/admin/orders')
def view_orders():
    conn = get_db()
    cursor = conn.cursor(dictionary=True)

        # 步驟 1: 查詢所有訂單的主資訊
        # 這裡我們只選擇 orders 表的欄位，因為我們要手動聚合 items
        # 確保 order_id 是主鍵，並且 order_datetime 和 total_amount 是訂單總覽的資訊
    cursor.execute("""
            SELECT
            o.ID AS order_id,
            o.order_datetime,
            o.total_amount
        FROM
            orders o
        ORDER BY o.order_datetime DESC
    """)
    raw_orders = cursor.fetchall()

    print(f"DEBUG: 原始訂單數量: {len(raw_orders)}") # 除錯點 1：確認是否抓到訂單
    print(f"DEBUG: 原始訂單內容: {raw_orders}") # 新增這行來確認

            # 儲存最終處理後的訂單資料
    processed_orders = []

    for order in raw_orders:
        order_id = order['order_id']
        
            # 步驟 2: 對於每一筆訂單，查詢其包含的所有品項
        cursor.execute("""
            SELECT
                oi.quantity,
                mi.name AS item_name,
                mi.price AS unit_price, -- 獲取單價以便重新計算總金額 (如果需要的話)
                oi.menu_item_id -- 也請確保這裡選取了 menu_item_id
            FROM
                order_items oi
            JOIN
                menu_items mi ON oi.menu_item_id = mi.ID
            WHERE
                oi.order_id = %s
        """, (order_id,))
        # 將每個訂單的品項清單儲存下來
        order_items_list_for_current_order = cursor.fetchall() # 變數名稱稍微調整，避免混淆

        print(f"DEBUG: 訂單 ID {order_id} 的品項數量: {len(order_items_list_for_current_order)}") # 除錯點 2：確認是否抓到品項

        # 步驟 3: 彙整品項資訊
        item_names_for_display = [] # 用於在「品項名稱」欄位中顯示，不帶數量
        total_quantity = 0

         # 儲存單個品項的詳細資訊列表，用於前端遍歷
        items_for_template = [] 
            
        # 如果 total_amount 在 orders 表中是可靠的總金額，就直接使用它
        # 如果 total_amount 需要根據 order_items 重新計算，則可以在這裡計算
        # 例如: recalculated_total_amount = 0

        if order_items_list_for_current_order:
            for item in order_items_list_for_current_order:
                    item_names_for_display.append(item['item_name']) # 只添加品項名稱
                    total_quantity += item['quantity'] # 累計總數量
        else:
            item_names_for_display.append("無品項")
            total_quantity = 0 # 沒有品項時總數量為0
                    # recalculated_total_amount += (item['quantity'] * item['unit_price'])

         # 確保 order_datetime 是可格式化的日期對象
        if isinstance(order['order_datetime'], (datetime, date)):
            formatted_datetime = order['order_datetime'].strftime('%Y-%m-%d %H:%M:%S')
        else:
            formatted_datetime = str(order['order_datetime']) # 如果不是日期對象，轉換為字串



                # 將處理後的資料添加到列表
        processed_orders.append({
            'order_id': order['order_id'],
            'order_datetime': order['order_datetime'],
            'total_amount': order['total_amount'], # 使用 orders 表中的總金額
            'total_amount': f"{order['total_amount']:.2f}", # 如果想重新計算總金額
            'item_names_display': ", ".join(item_names_for_display), # 用逗號分隔多個品項
            'total_quantity': total_quantity,
        })

    print(f"DEBUG: 最終處理後的訂單資料: {processed_orders}") # 除錯點 3：查看最終傳給模板的資料

            # 將處理後的資料傳遞給模板
    return render_template('employee_order_view.html', orders=processed_orders)


# API 更新訂單 (編輯)
@app.route('/employee/orders/edit')
def edit_order_page():
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    orders =[]

    try:
        # 步驟 1: 查詢所有訂單的主資訊
        cursor.execute("""
            SELECT
                o.id AS order_id,
                o.order_datetime,
                o.total_amount
            FROM
                orders o
            ORDER BY o.order_datetime DESC
        """)
        raw_orders = cursor.fetchall()

        processed_orders = []

        for order in raw_orders:
            order_id = order['order_id']
            
            # 步驟 2: 對於每一筆訂單，查詢其包含的所有品項詳情
            # 我們需要知道每個品項的單價來計算總金額
            cursor.execute("""
                SELECT 
                    oi.id AS order_item_id, 
                    oi.quantity, 
                    mi.name AS item_name,
                    mi.price AS unit_price
                FROM order_items oi
                JOIN menu_items mi ON oi.menu_item_id = mi.id
                WHERE oi.order_id = %s
            """, (order_id,))
            order_items_detail = cursor.fetchall()

            item_names_combined_str = [] 
            total_quantity = 0
            
            # 由於您圖片的「數量」欄位只有一個，我們這裡假設只修改「第一筆品項」的數量，
            # 或是您可以設定一個「主品項」來更新。
            # 為了簡化，我們假定前端的 quantity input 總是修改該訂單的「第一筆品項」的數量。
            # 如果訂單有多個品項，但只有一個可編輯的「數量」框，這是一個業務邏輯上的限制。
            # 建議：如果訂單有多個品項，最好跳轉到詳細編輯頁面。
            
            # 我們傳遞訂單的第一個品項的數量和單價，以便前端可以直接修改和計算
            first_item_quantity = 0
            first_item_unit_price = 0.0

            if order_items_detail:
                # 獲取第一個品項的數據
                first_item_quantity = order_items_detail[0]['quantity']
                first_item_unit_price = float(order_items_detail[0]['unit_price'])

                for item in order_items_detail:
                    item_names_combined_str.append(f"{item['item_name']}*{item['quantity']}")
                    total_quantity += item['quantity']
            else:
                item_names_combined_str.append("無品項")

            processed_orders.append({
                'order_id': order['order_id'],
                'order_datetime': order['order_datetime'],
                'total_amount': order['total_amount'], # 這是資料庫中存儲的原始總金額
                'item_names_combined': ", ".join(item_names_combined_str),
                'total_quantity': total_quantity, # 這是所有品項的總和
                'items_detail': order_items_detail, # 詳細品項列表
                'editable_quantity': first_item_quantity, # 可編輯的數量（假定為第一個品項的數量）
                'editable_unit_price': first_item_unit_price # 對應的單價
            })
        
        print(f"DEBUG: 傳遞給 employee_edit_orders.html 的訂單數據: {processed_orders}") 

        return render_template('employee_edit_orders.html', orders=processed_orders)

    except Exception as e:
        print(f"ERROR in edit_order_page: {e}")
        flash(f"載入訂單數據失敗: {e}", "danger")
 

    finally:
        cursor.close()
        conn.close()

    return render_template('employee_edit_orders', orders=orders)


@app.route('/employee/orders/delete/<int:order_id>', methods=['POST'])
def delete_order(order_id):
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM order_items WHERE order_id = %s", (order_id,))
        cursor.execute("DELETE FROM orders WHERE id = %s", (order_id,))
        conn.commit()
    except Exception as e:
        conn.rollback()
        print("刪除失敗:", e)
    finally:
        cursor.close()
    return redirect(url_for('edit_order_page'))


@app.route('/employee/orders/update/<int:order_id>', methods=['POST'])
def update_order(order_id):
    conn = get_db()
    cursor = conn.cursor(dictionary=True) # 使用 dictionary=True 方便獲取字段

    try:
        new_quantity_str = request.form.get('quantity')
        
        if not new_quantity_str:
            flash('未提供新的數量', 'danger')
            return redirect(url_for('emolyee_edit_orders'))

        new_quantity = int(new_quantity_str)
        if new_quantity < 0:
            flash('數量不能為負數', 'danger')
            return redirect(url_for('emolyee_edit_orders'))

        conn.start_transaction()

        # 獲取該訂單的第一個品項的 order_item_id 和 unit_price
        cursor.execute("""
            SELECT oi.id AS order_item_id, mi.price AS unit_price
            FROM order_items oi
            JOIN menu_items mi ON oi.menu_item_id = mi.id
            WHERE oi.order_id = %s
            ORDER BY oi.id ASC # 確保始終獲取同一個「第一個品項」
            LIMIT 1
        """, (order_id,))
        first_order_item = cursor.fetchone()

        if not first_order_item:
            flash(f'訂單 {order_id} 中沒有可更新的品項。', 'danger')
            conn.rollback()
            return redirect(url_for('edit_order_page'))

        item_id_to_update = first_order_item['order_item_id']
        unit_price = float(first_order_item['unit_price'])

        # 更新 order_items 表中該品項的數量
        cursor.execute("UPDATE order_items SET quantity = %s WHERE id = %s", 
                       (new_quantity, item_id_to_update))

        # 重新計算訂單的總金額 (假設只有一個品項或總金額由這單一品項決定)
        # 如果訂單有多個品項，這裡需要更複雜的邏輯來計算新的 total_amount
        # 例如：sum(oi.quantity * mi.price for all items in order_id)
        cursor.execute("""
            SELECT SUM(oi.quantity * mi.price) AS calculated_total_amount
            FROM order_items oi
            JOIN menu_items mi ON oi.menu_item_id = mi.id
            WHERE oi.order_id = %s
        """, (order_id,))
        calculated_total = cursor.fetchone()['calculated_total_amount']
        
        # 更新 orders 表的總金額
        cursor.execute("UPDATE orders SET total_amount = %s WHERE id = %s", 
                       (calculated_total, order_id))
        
        conn.commit()
        flash('訂單更新成功！', 'success')

    except ValueError:
        flash('數量格式不正確，必須是整數', 'danger')
        conn.rollback()
    except Exception as e:
        conn.rollback()
        print(f"更新失敗: {e}")
        flash(f'訂單更新失敗: {e}', 'danger')
    finally:
        cursor.close()
        conn.close()
    
    return redirect(url_for('edit_order_page')) # 更新後重定向回列表頁面




@app.route('/api/update_inventory', methods=['POST', 'PUT'])
def update_inventory():
    conn = get_db()
    cursor = conn.cursor()
    try:

        data = request.get_json()
        print("收到的 JSON:", data)
        ingredient_id = request.form.get('id')
        new_stock_quantity = request.form.get('stock_quantity')
        unit = request.form.get('unit')   
        
        if ingredient_id is None or new_stock_quantity is None:
            return jsonify({'message': '缺少必要參數', 'success': False}), 400
        
        # 數值轉換
        try:
            new_stock_quantity = float(new_stock_quantity)
        except ValueError:
            return jsonify({'message': '庫存數量必須是數字', 'success': False}), 400


        print("更新庫存參數:", new_stock_quantity, ingredient_id)
        print("參數類型:", type(new_stock_quantity), type(ingredient_id))

        update_query = """
            UPDATE ingredient_inventory
            SET stock_quantity = %s,
                unit = %s
            WHERE ID = %s
        """
        cursor.execute(update_query, (new_stock_quantity, unit, ingredient_id))
        conn.commit()
        return jsonify({'message': '庫存更新成功', 'success': True}), 200

    except mysql.connector.Error as err:
        conn.rollback()
        print(f"資料庫錯誤 (更新庫存): {err}")
        return jsonify({'message': f'資料庫錯誤 (更新庫存): {err}', 'success': False}), 500
    except Exception as e:
        conn.rollback()
        print(f"更新庫存錯誤: {e}")
        return jsonify({'message': f'更新庫存失敗: {e}', 'success': False}), 500
    finally:
        cursor.close()
        conn.close()


@app.route('/admin/inventory')
def admin_inventory():
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT ID, ingredient_name, stock_quantity, unit, safety_stock
            FROM ingredient_inventory
            ORDER BY ingredient_name
        """)
        inventory = cursor.fetchall()
    except mysql.connector.Error as err:
        print(f"資料庫錯誤 (admin_inventory): {err}")
        inventory = []
    finally:
        cursor.close()
        conn.close()
    return render_template('admin_inventory.html', inventory=inventory)

@app.route('/admin/inventory/manage', methods=['GET', 'POST'])
def admin_inventory_manage():
    if request.method == 'POST':
        conn = None
        cursor = None

        try:
            conn = get_db()
            cursor = conn.cursor(dictionary=True, buffered=True)

            action = request.form.get('action')  # 'add', 'update', 'delete'
            ingredient_id = request.form.get('id')
            ingredient_name = request.form.get('ingredient_name')
            stock_quantity = float(request.form.get('stock_quantity'))
            unit = request.form.get('unit')
            # 獲取安全庫存量和過期日
            safety_stock = int(request.form.get('safety_stock')) # 安全庫存量通常是整數
            expiration_date_str = request.form.get('expiration_date')
            expiration_date = None
            if expiration_date_str:
                expiration_date = datetime.strptime(expiration_date_str, '%Y-%m-%d').date() # 將日期字符串轉換為日期物件

            if action == 'add':
                # 先檢查資料庫中是否已有相同食材名稱
                cursor.execute("SELECT ID, stock_quantity FROM ingredient_inventory WHERE ingredient_name = %s", (ingredient_name,))
                existing = cursor.fetchone()

                if existing:
                    # 如果有，更新數量（加總）
                    new_quantity = float(existing['stock_quantity']) + float(stock_quantity)
                    cursor.execute("""
                        UPDATE ingredient_inventory
                        SET stock_quantity = %s, unit = %s, safety_stock = %s, expiration_date = %s
                        WHERE ID = %s
                    """, (new_quantity, unit, safety_stock, expiration_date, existing['ID']))
                    flash('已合併相同食材，更新庫存成功', 'success')
                else:
                    # 如果沒有，執行新增
                    cursor.execute("""
                        INSERT INTO ingredient_inventory (ingredient_name, stock_quantity, unit, safety_stock, expiration_date)
                        VALUES (%s, %s, %s, %s, %s)
                    """, (ingredient_name, stock_quantity, unit, safety_stock, expiration_date))
                    flash('新增成功', 'success')

                purchase_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                cursor.execute("""
                    INSERT INTO purchases (purchase_date, item_name, quantity, unit)
                    VALUES (%s, %s, %s, %s)
                    """, (purchase_time, ingredient_name, stock_quantity, unit))

            elif action == 'update':
                cursor.execute("""
                    UPDATE ingredient_inventory
                    SET ingredient_name=%s, stock_quantity=%s, unit=%s, safety_stock = %s, expiration_date = %s
                    WHERE ID=%s
                """, (ingredient_name, stock_quantity, unit, safety_stock, expiration_date, ingredient_id))
                conn.commit()
                flash('修改成功', 'success')

            elif action == 'delete':
                cursor.execute("DELETE FROM ingredient_inventory WHERE ID=%s", (ingredient_id,))
                conn.commit()
                flash('刪除成功', 'success')

            else:
                flash('不明操作', 'danger')

            conn.commit()  

        except mysql.connector.Error as err:
            flash(f'資料庫錯誤: {err}', 'danger')

        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()

        return redirect(url_for('admin_inventory_manage'))

    else:
        conn = None
        cursor = None
        # GET 請求，讀取庫存資料並顯示管理頁面
        try:
            conn = get_db()
            cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT ID, ingredient_name, stock_quantity, unit, safety_stock, expiration_date FROM ingredient_inventory ORDER BY ingredient_name")
            inventory = cursor.fetchall()
        except mysql.connector.Error as err:
            inventory = []
            flash(f'資料庫錯誤: {err}', 'danger')
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()


        return render_template('admin_inventory_manage.html', inventory=inventory)
    
@app.route('/admin/purchases/manage', methods=['GET', 'POST'])
def admin_purchases_manage():
    conn = get_db()
    cursor = conn.cursor(dictionary=True)

    cursor.execute(""" 
        SELECT ID, purchase_date, item_name, quantity, unit 
        FROM purchases 
        ORDER BY purchase_date
    """)
    purchases = cursor.fetchall()
    cursor.close()
    conn.close()

    return render_template('admin_purchases_manage.html', purchases=purchases)

@app.route('/admin/purchases/update/<int:purchase_id>', methods=['POST'])
def update_purchase(purchase_id):
    item_name = request.form['item_name']
    quantity = request.form['quantity']
    unit = request.form['unit']

    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            UPDATE purchases 
            SET item_name = %s, quantity = %s, unit = %s 
            WHERE ID = %s
        """, (item_name, quantity, unit, purchase_id))
        conn.commit()
        flash('進貨資料更新成功', 'success')
    except Exception as e:
        conn.rollback()
        print("更新失敗:", e)
        flash('更新失敗', 'danger')
    finally:
        cursor.close()
    return redirect(url_for('admin_purchases_manage'))


@app.route('/admin/purchases/delete/<int:purchase_id>', methods=['POST'])
def delete_purchase(purchase_id):
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM purchases WHERE ID = %s", (purchase_id,))
        conn.commit()
        flash('刪除成功', 'success')
    except Exception as e:
        conn.rollback()
        print("刪除失敗:", e)
        flash('刪除失敗', 'danger')
    finally:
        cursor.close()
    return redirect(url_for('admin_purchases_manage'))


@app.route('/admin/orders')
def admin_orders():
    print("進入 admin_orders 頁面")
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    orders = []

    try:
        # 查詢所有訂單及其詳細信息
        query = """
            SELECT 
                o.id AS order_id,
                o.order_datetime,
                o.total_amount,
                oi.quantity
            FROM orders o
            LEFT JOIN order_items oi ON o.id = oi.order_id
            LEFT JOIN menu_items mi ON oi.menu_item_id = mi.id
            ORDER BY o.order_datetime DESC
        """
        cursor.execute(query)
        orders = cursor.fetchall()
        print("取回的訂單：", orders) 

        for order in orders:
            if isinstance(order['order_datetime'], (datetime, date)):
                order['order_datetime'] = order['order_datetime'].strftime('%Y-%m-%d %H:%M:%S')

            # 查詢訂單明細
            items_query = """
                SELECT 
                    mi.name AS flavor,
                    oi.quantity,
                    mi.price
                FROM order_items oi
                JOIN menu_items mi ON oi.menu_item_id = mi.id
                WHERE oi.order_id = %s
            """
            cursor.execute(items_query, (order['order_id'],))
            order['items'] = cursor.fetchall()
            order['items_display'] = ", ".join([f"{item['flavor']} x {item['quantity']}" for item in order['items']])

    except mysql.connector.Error as err:
        print(f"資料庫錯誤 (admin_orders): {err}")
    finally:
        cursor.close()
        conn.close()

    return render_template('employee_orders.html', orders=orders)


# 訂單編輯詳情頁面 (GET)
@app.route('/admin/order_detail/<int:order_id>', methods=['GET'])
def admin_order_detail(order_id):
    order = None
    order_items = []
    menu_items = []
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT id, name, price FROM menu_items")
        menu_items = cursor.fetchall()

        cursor.execute("SELECT id, order_datetime, total_amount FROM orders WHERE id = %s", (order_id,))
        order = cursor.fetchone()
        
        if order:
            if isinstance(order['order_datetime'], (datetime, date)):
                order['order_datetime'] = order['order_datetime'].strftime('%Y-%m-%d')

            cursor.execute("""
                SELECT oi.id AS order_item_id, oi.menu_item_id, mi.name AS flavor, oi.quantity, mi.price
                FROM order_items oi
                JOIN menu_items mi ON oi.menu_item_id = mi.id
                WHERE oi.order_id = %s
            """, (order_id,))
            order_items = cursor.fetchall()

    except mysql.connector.Error as err:
        print(f"資料庫錯誤 (admin_order_detail): {err}")
    finally:
        cursor.close()
        conn.close()
    return render_template('admin_order_detail.html', order=order, order_items=order_items, menu_items=menu_items)

@app.route('/admin/menu/manage', methods=['GET', 'POST'])
def admin_menu_manage():
    conn = None
    cursor = None

    if request.method == 'POST':
        try:
            conn = get_db()
            cursor = conn.cursor(dictionary=True, buffered=True)

            action = request.form.get('action') # 'add', 'update', 'delete'
            menu_id = request.form.get('id')
            name = request.form.get('name')
            price = request.form.get('price')

            if action == 'add':
                # 檢查是否已存在相同菜單名稱
                cursor.execute("SELECT id FROM menu_items WHERE name = %s", (name,))
                existing_menu_item = cursor.fetchone()

                if existing_menu_item:
                    flash('菜單項目已存在，請使用修改功能或更改名稱', 'danger')
                else:
                    cursor.execute("""
                        INSERT INTO menu_items (name, price)
                        VALUES (%s, %s)
                    """, (name, price))
                    conn.commit()
                    flash('菜單項目新增成功', 'success')
                
            elif action == 'update':
                if menu_id:
                    cursor.execute("""
                        UPDATE menu_items
                        SET name = %s, price = %s
                        WHERE ID = %s
                    """, (name, price, menu_id))
                    conn.commit()
                    flash('菜單項目修改成功', 'success')
                else:
                    flash('缺少菜單項目ID，無法修改', 'danger')

            elif action == 'delete':
                if menu_id:
                    cursor.execute("DELETE FROM menu_items WHERE ID = %s", (menu_id,))
                    conn.commit()
                    flash('菜單項目刪除成功', 'success')
                else:
                    flash('缺少菜單項目ID，無法刪除', 'danger')

            else:
                flash('不明操作', 'danger')

        except mysql.connector.Error as err:
            flash(f'資料庫錯誤: {err}', 'danger')
            if conn:
                conn.rollback() # 錯誤時回滾事務
        except ValueError:
            flash('價格必須是有效的數字', 'danger')
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()
        return redirect(url_for('admin_menu_manage')) # 處理完POST請求後重定向
    
    else: # GET request
        try:
            conn = get_db()
            cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT ID, name, price FROM menu_items ORDER BY name")
            menu_items = cursor.fetchall()
        except mysql.connector.Error as err:
            menu_items = []
            flash(f'資料庫錯誤: {err}', 'danger')
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()
        return render_template('admin_menu_manage.html', menu_items=menu_items)
    
@app.route('/admin/menu/recipe/<int:menu_item_id>', methods=['GET', 'POST'])
def admin_menu_recipe_manage(menu_item_id):
    conn = None
    cursor = None
    menu_item = None # 儲存菜單項目的名稱，以便在頁面顯示
    product_ingredients = [] # 儲存該菜單項目的所有配方食材
    all_ingredients = [] # 儲存所有可用的庫存食材

    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True, buffered=True)

        # 1. 獲取當前菜單項目的資訊 (例如名稱)
        cursor.execute("SELECT id, name FROM menu_items WHERE ID = %s", (menu_item_id,))
        menu_item = cursor.fetchone()
        if not menu_item:
            flash('菜單項目不存在', 'danger')
            return redirect(url_for('admin_menu_manage')) # 如果菜單項目不存在，重定向回菜單管理頁面

        if request.method == 'POST':
            action = request.form.get('action')
            ingredient_id = request.form.get('ingredient_id')
            quantity_needed = request.form.get('quantity_needed') # <-- 這裡從 quantity_used 改為 quantity_needed
            unit = request.form.get('unit') # 這個單位是配方用量單位，不是庫存單位
            recipe_id = request.form.get('recipe_id') # 用於修改/刪除 product_ingredients 的 ID

             # 針對 'ingredient_id' 進行檢查和轉換
            if ingredient_id:
                try:
                    ingredient_id = int(ingredient_id) # 嘗試將其轉換為整數
                except ValueError:
                    flash('選擇的食材 ID 無效。', 'danger')
                    return redirect(url_for('admin_menu_recipe_manage', menu_item_id=menu_item_id))
            else:
                # 如果 ingredient_id 為空，則顯示錯誤訊息並重定向
                flash('請選擇一個食材。', 'danger')
                return redirect(url_for('admin_menu_recipe_manage', menu_item_id=menu_item_id))


            # 處理數據類型轉換和驗證
            try:
                if quantity_needed:
                    quantity_needed = float(quantity_needed) # <-- 這裡從 quantity_used 改為 quantity_needed
                    if quantity_needed < 0:
                        raise ValueError("用量不能為負數")
                else:
                    quantity_needed = 0.0 # 或者根據您的業務邏輯設定預設值
            except ValueError:
                flash('用量必須是有效的數字且不能為負', 'danger')
                return redirect(url_for('admin_menu_recipe_manage', menu_item_id=menu_item_id))
            
            if action == 'add':
                # 檢查該食材是否已經存在於此菜單項目中 (product_ingredients 表)
                cursor.execute("SELECT id FROM product_ingredients WHERE menu_item_id = %s AND ingredient_id = %s", (menu_item_id, ingredient_id))
                if cursor.fetchone():
                    flash('該食材已存在於此菜單項目配方中，請直接修改', 'warning')
                else:
                    cursor.execute("""
                        INSERT INTO product_ingredients (menu_item_id, ingredient_id, quantity_needed, unit)
                        VALUES (%s, %s, %s, %s)
                    """, (menu_item_id, ingredient_id, quantity_needed, unit))
                    conn.commit()
                    flash('配方食材新增成功', 'success')

            elif action == 'update':
                if recipe_id:
                    cursor.execute("""
                        UPDATE product_ingredients
                        SET ingredient_id = %s, quantity_needed = %s, unit = %s
                        WHERE id = %s AND menu_item_id = %s
                    """, (ingredient_id, quantity_needed, unit, recipe_id, menu_item_id))
                    conn.commit()
                    flash('配方食材修改成功', 'success')
                else:
                    flash('缺少配方ID，無法修改', 'danger')

            elif action == 'delete':
                if recipe_id:
                    cursor.execute("DELETE FROM product_ingredients WHERE id = %s AND menu_item_id = %s", (recipe_id, menu_item_id))
                    conn.commit()
                    flash('配方食材刪除成功', 'success')
                else:
                    flash('缺少配方ID，無法刪除', 'danger')
            else:
                flash('不明操作', 'danger')

            # 重定向以清除表單並顯示最新數據
            return redirect(url_for('admin_menu_recipe_manage', menu_item_id=menu_item_id))
        
        else: # GET request
            # 2. 獲取所有庫存食材供選擇
            cursor.execute("SELECT id, ingredient_name, unit FROM ingredient_inventory ORDER BY ingredient_name")
            all_ingredients = cursor.fetchall()

            # 3. 獲取當前菜單項目的所有配方食材
            cursor.execute("""
                SELECT
                    pi.id AS recipe_id,             -- product_ingredients 表的 ID (用 'id' 而非 'ID')
                    pi.quantity_needed,             -- 用量
                    pi.unit,
                    ii.id AS ingredient_id,         -- ingredient_inventory 表的 ID
                    ii.ingredient_name
                FROM product_ingredients pi
                JOIN ingredient_inventory ii ON pi.ingredient_id = ii.id
                WHERE pi.menu_item_id = %s
                ORDER BY ii.ingredient_name
            """, (menu_item_id,))
            product_ingredients = cursor.fetchall()

    except mysql.connector.Error as err:
        flash(f'資料庫錯誤: {err}', 'danger')
        if conn:
            conn.rollback() # 發生錯誤時回滾
    except Exception as e:
        flash(f'發生錯誤: {e}', 'danger')
        if conn:
            conn.rollback()
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

    return render_template(
        'admin_menu_recipe_manage.html',
        menu_item=menu_item,
        product_ingredients=product_ingredients,
        all_ingredients=all_ingredients
    )


@app.route('/admin')
def admin_home():
    return render_template('admin_home.html')


@app.route('/admin/sales-analysis')
def admin_sales_analysis():
    conn = get_db()
    cursor = conn.cursor(dictionary=True)

    # 獲取 URL 參數中的開始日期和結束日期
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')

    # 使用列表來構建 SQL 查詢，更安全地處理動態部分
    sql_query_parts = [
        "SELECT",
        "    mi.name AS item_name,",
        "    SUM(oi.quantity) AS total_quantity_sold,",
        "    SUM(oi.quantity * mi.price) AS total_sales_amount",
        "FROM",
        "    order_items oi",
        "JOIN",
        "    menu_items mi ON oi.menu_item_id = mi.id",
        "JOIN",
        "    orders o ON oi.order_id = o.id"  # 假設 order_items 透過 order_id 關聯到 orders 表
    ]

    # 準備 SQL 查詢參數
    params = []
    where_clauses = []

    if start_date:
        where_clauses.append("o.order_datetime >= %s")
        params.append(start_date)
    if end_date:
        # 如果是 datetime 欄位，確保包含結束日期的整天
        where_clauses.append("o.order_datetime <= %s")
        params.append(end_date + ' 23:59:59') # 包含結束日期的所有時間

    if where_clauses:
        sql_query_parts.append("WHERE")
        sql_query_parts.append(" AND ".join(where_clauses))

    sql_query_parts.append("GROUP BY")
    sql_query_parts.append("    mi.name")
    sql_query_parts.append("ORDER BY")
    sql_query_parts.append("    total_quantity_sold DESC;")

    # 將所有部分合併成最終的 SQL 查詢字串
    sql_query = " ".join(sql_query_parts)

    cursor.execute(sql_query, tuple(params))
    sales_data = cursor.fetchall()
  

    cursor.close()
    conn.close()

    return render_template('admin_sales_analysis.html', sales_data=sales_data)


if __name__ == '__main__':
    app.run(debug=True)
